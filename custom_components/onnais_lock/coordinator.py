"""Polling coordinator for a single ONNAIS lock config entry."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .lock_api import OnnaisAuthError, OnnaisCommandError, OnnaisLockClient

_LOGGER = logging.getLogger(__name__)


RECONCILE_INTERVAL = timedelta(minutes=10)
# This is a real trade-off, not a free win: this lock can be operated by a
# physical key or key fob that Home Assistant has no way to find out about
# except by actually connecting and asking -- and every connection makes
# the lock audibly chirp once. A short interval (as this used to be) means
# frequent beeping for no practical benefit; no periodic check at all (as
# this later became, briefly) means the displayed state can silently drift
# wrong indefinitely after an external lock/unlock and never self-correct.
# Ten minutes is a middle ground: state can be stale for at most that long
# between an actual lock/unlock/service call, and the lock beeps at most
# six times an hour instead of every 60-90 seconds.


class OnnaisCoordinator(DataUpdateCoordinator[dict]):
    """Fetches lock/battery/buzzer/keypad-light/code status, and is also
    the shared place entities and services reach the BLE client through, so
    every connection goes through one coordinated session at a time rather
    than racing concurrent BLE connects to the same radio.

    Status also refreshes immediately after every lock/unlock or service
    call (``async_run_command`` below), on top of the periodic interval --
    that call doesn't need to wait for the next tick to confirm what it
    just did.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, address: str, secret: bytes) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"onnais_lock_{address}",
            update_interval=RECONCILE_INTERVAL,
        )
        self.address = address
        self.entry = entry
        ble_device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
        if ble_device is None:
            raise ConfigEntryNotReady(f"ONNAIS lock {address} not currently visible to Home Assistant's Bluetooth stack")
        self.client = OnnaisLockClient(ble_device, secret)

    def _refresh_ble_device(self) -> None:
        ble_device = bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)
        if ble_device is not None:
            self.client.update_ble_device(ble_device)

    async def _async_update_data(self) -> dict:
        self._refresh_ble_device()
        try:
            return await self.client.get_status()
        except OnnaisAuthError as error:
            raise ConfigEntryNotReady(
                "ONNAIS lock rejected its stored secret -- it may have been "
                "unbound/rebound outside Home Assistant. Remove and re-add "
                "this lock to re-claim it."
            ) from error
        except OnnaisCommandError as error:
            raise UpdateFailed(str(error)) from error
        except Exception as error:  # noqa: BLE001 - any BLE/transport failure (BleakError,
            # TimeoutError, OSError, the "le-connection-abort-by-local" family, etc.) must
            # become UpdateFailed, never an unhandled exception out of a coordinator update.
            raise UpdateFailed(f"BLE error talking to lock: {error}") from error

    async def async_run_command(self, coro_factory):
        """Run a write command then force an immediate status refresh so
        entities reflect the lock's own confirmed state, not just the
        command's local success. The refresh still runs even if the
        command itself raised: a reply can get lost in transit after the
        lock has already physically acted on it, so checking the real
        state afterward beats trusting our own success/failure guess."""
        self._refresh_ble_device()
        try:
            await coro_factory()
        finally:
            await self.async_request_refresh()
