"""Config flow: HA's native Bluetooth discovery wizard for ONNAIS locks.

Two paths into setup:

* Automatic -- Home Assistant's Bluetooth integration passively matches the
  lock's advertised name (see ``manifest.json``'s ``bluetooth`` matcher) and
  calls ``async_step_bluetooth`` with the discovery info. The user confirms,
  puts the lock in pairing mode, and we claim it (generate + set a new local
  secret) right there, exactly like the standalone tooling this integration
  was developed from.
* Manual -- ``async_step_user`` lets you pick from currently-visible ONNAIS
  advertisements, and also accepts an existing hex secret so a lock already
  claimed by this project's command-line tooling (or a previous HA setup)
  can be imported without pairing it again.
"""

from __future__ import annotations

import asyncio
import logging
import secrets

import voluptuous as vol
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_SECRET, DOMAIN, FACTORY_DEFAULT_SECRET, MANUFACTURER_ID
from .lock_api import OnnaisAuthError, OnnaisLockClient

_LOGGER = logging.getLogger(__name__)

MANUAL_SCAN_SECONDS = 25


def _generate_secret() -> bytes:
    return secrets.token_hex(16).encode()


def _is_onnais_advertisement(info: bluetooth.BluetoothServiceInfoBleak) -> bool:
    """True if this advertisement is from an ONNAIS lock.

    Matching on name alone is not reliable: that field is only populated
    when a scanner does active scanning and merges in the device's scan
    response (observed via BlueZ talking to the lock directly). Some
    Bluetooth proxies relay only the bare primary advertisement packet,
    which has no name at all -- but that packet always carries this
    vendor's manufacturer ID, so that's checked too.
    """
    if info.name and info.name.startswith("MH_safe"):
        return True
    return MANUFACTURER_ID in info.manufacturer_data


class OnnaisConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovery_info: bluetooth.BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, str] = {}
        self._scan_task: asyncio.Task | None = None

    async def async_step_bluetooth(self, discovery_info: bluetooth.BluetoothServiceInfoBleak) -> FlowResult:
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name or discovery_info.address}
        return await self.async_step_confirm()

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        """Entry point: show a live "Scanning..." progress step instead of
        either an unlabelled spinner or an instant false negative, since this
        lock's advertisements are sparse rather than continuous."""
        if self._scan_task is None:
            current_addresses = self._async_current_ids()
            for info in bluetooth.async_discovered_service_info(self.hass, connectable=True):
                if info.address in current_addresses or info.address in self._discovered_devices:
                    continue
                if _is_onnais_advertisement(info):
                    self._discovered_devices[info.address] = f"{info.name or info.address} ({info.address})"

            if not self._discovered_devices:
                self._scan_task = self.hass.async_create_task(
                    self._async_wait_for_advertisement(current_addresses)
                )

        if self._scan_task is not None:
            if not self._scan_task.done():
                return self.async_show_progress(
                    step_id="user",
                    progress_action="scan_bluetooth",
                    progress_task=self._scan_task,
                )
            self._scan_task = None
            if not self._discovered_devices:
                return self.async_show_progress_done(next_step_id="no_devices_found")
            return self.async_show_progress_done(next_step_id="pick_device")

        return await self.async_step_pick_device()

    async def _async_wait_for_advertisement(self, current_addresses: set[str]) -> None:
        """Actively listen for a live advertisement for MANUAL_SCAN_SECONDS.

        A snapshot of HA's cached discoveries often comes back empty even
        when the lock is powered and in range, since it advertises in short,
        sparse bursts rather than continuously -- so this waits for a live
        match (or the timeout) instead of failing instantly.
        """
        found = asyncio.Event()

        @callback
        def _on_advertisement(info: bluetooth.BluetoothServiceInfoBleak, change) -> None:
            if info.address in current_addresses or info.address in self._discovered_devices:
                return
            if not _is_onnais_advertisement(info):
                return
            self._discovered_devices[info.address] = f"{info.name or info.address} ({info.address})"
            found.set()

        unregister_name = bluetooth.async_register_callback(
            self.hass,
            _on_advertisement,
            bluetooth.BluetoothCallbackMatcher(connectable=True, local_name="MH_safe*"),
            bluetooth.BluetoothScanningMode.ACTIVE,
        )
        unregister_mfr = bluetooth.async_register_callback(
            self.hass,
            _on_advertisement,
            bluetooth.BluetoothCallbackMatcher(connectable=True, manufacturer_id=MANUFACTURER_ID),
            bluetooth.BluetoothScanningMode.ACTIVE,
        )
        try:
            await asyncio.wait_for(found.wait(), timeout=MANUAL_SCAN_SECONDS)
        except TimeoutError:
            pass
        finally:
            unregister_name()
            unregister_mfr()

    async def async_step_no_devices_found(self, user_input: dict | None = None) -> FlowResult:
        return self.async_abort(reason="no_devices_found")

    async def async_step_pick_device(self, user_input: dict | None = None) -> FlowResult:
        if user_input is not None:
            address = user_input["address"]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            self._discovery_info = bluetooth.async_last_service_info(self.hass, address, connectable=True)
            if self._discovery_info is None:
                return self.async_abort(reason="not_visible")
            if user_input.get(CONF_SECRET):
                try:
                    secret = bytes.fromhex(user_input[CONF_SECRET].strip())
                except ValueError:
                    secret = b""
                if not secret:
                    return self._pick_device_form({"base": "invalid_secret"})
                return await self._finish_with_existing_secret(address, secret)
            return await self.async_step_confirm()

        return self._pick_device_form()

    def _pick_device_form(self, errors: dict | None = None) -> FlowResult:
        return self.async_show_form(
            step_id="pick_device",
            data_schema=vol.Schema(
                {
                    vol.Required("address"): vol.In(self._discovered_devices),
                    vol.Optional(CONF_SECRET): str,
                }
            ),
            errors=errors or {},
        )

    async def async_step_confirm(self, user_input: dict | None = None) -> FlowResult:
        """Claim the lock: put it in pairing mode, we authenticate with the
        vendor default secret and immediately replace it with a fresh,
        locally-generated one that never leaves this Home Assistant install."""
        assert self._discovery_info is not None
        if user_input is None:
            return self.async_show_form(
                step_id="confirm",
                description_placeholders={
                    "name": self._discovery_info.name or self._discovery_info.address,
                    "address": self._discovery_info.address,
                },
            )

        new_secret = _generate_secret()
        try:
            await OnnaisLockClient.claim(self._discovery_info.device, new_secret, FACTORY_DEFAULT_SECRET)
        except OnnaisAuthError:
            return self.async_show_form(
                step_id="confirm",
                errors={"base": "already_bound"},
                description_placeholders={
                    "name": self._discovery_info.name or self._discovery_info.address,
                    "address": self._discovery_info.address,
                },
            )
        except Exception as error:  # noqa: BLE001
            # Deliberately broad: bleak surfaces transport failures (e.g. the
            # "le-connection-abort-by-local" family) as bleak.exc.BleakError,
            # which is neither OSError nor TimeoutError -- a config flow step
            # must never let an unhandled exception escape, it has to show
            # the "cannot_connect" form instead.
            _LOGGER.debug("ONNAIS claim attempt failed: %s", error)
            return self.async_show_form(
                step_id="confirm",
                errors={"base": "cannot_connect"},
                description_placeholders={
                    "name": self._discovery_info.name or self._discovery_info.address,
                    "address": self._discovery_info.address,
                },
            )

        return self.async_create_entry(
            title=self._discovery_info.name or self._discovery_info.address,
            data={"address": self._discovery_info.address, CONF_SECRET: new_secret.hex()},
        )

    async def _finish_with_existing_secret(self, address: str, secret: bytes) -> FlowResult:
        """Import a lock this project's own tooling (or a prior HA setup)
        already claimed -- no pairing-mode button press needed."""
        assert self._discovery_info is not None
        try:
            client = OnnaisLockClient(self._discovery_info.device, secret)
            await client.get_status()
        except Exception as error:  # noqa: BLE001 - see async_step_confirm
            _LOGGER.debug("ONNAIS secret import failed: %s", error)
            if address not in self._discovered_devices:
                self._discovered_devices[address] = address
            return self._pick_device_form({"base": "invalid_secret"})
        return self.async_create_entry(
            title=self._discovery_info.name or address,
            data={"address": address, CONF_SECRET: secret.hex()},
        )
