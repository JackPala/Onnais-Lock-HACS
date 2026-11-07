"""Coordinator for a single ONNAIS lock config entry."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .lock_api import OnnaisAuthError, OnnaisCommandError, OnnaisLockClient

_LOGGER = logging.getLogger(__name__)

AUTH_FAILED_MESSAGE = (
    "The lock rejected Home Assistant's stored secret -- it may have been "
    "unbound/rebound outside Home Assistant. Remove and re-add this lock."
)

HEALTH_CHECK_INTERVAL = timedelta(minutes=5)
# Not a routine poll. Every connection makes this lock chirp, so while the
# last status read succeeded these ticks are skipped entirely (no connection,
# no beep). They only do real work while the last read failed, or after a
# command whose outcome couldn't be confirmed -- so a bad moment on the radio
# self-corrects instead of leaving stale state indefinitely.


class OnnaisCoordinator(DataUpdateCoordinator[dict]):
    """Owns the BLE client for one lock and is the single path every
    status read and command goes through."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, address: str, secret: bytes) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"onnais_lock_{address}",
            update_interval=HEALTH_CHECK_INTERVAL,
        )
        self.address = address
        self.entry = entry
        self._force_refresh = True
        self._command_pending = False
        ble_device = self._lookup_ble_device()
        if ble_device is None:
            raise ConfigEntryNotReady(f"ONNAIS lock {address} not currently visible to Home Assistant's Bluetooth stack")
        self.client = OnnaisLockClient(ble_device, secret, ble_device_callback=self._lookup_ble_device)

    def _lookup_ble_device(self):
        return bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)

    async def _async_update_data(self) -> dict:
        if self.data is not None and self.last_update_success and not self._force_refresh:
            return self.data
        if self.client.busy or self._command_pending:
            # A command is running and reports fresh status itself; don't
            # queue a second connection behind it.
            if self.data is not None:
                return self.data
            raise UpdateFailed("Lock busy with another operation")
        try:
            data = await self.client.get_status(background=True)
        except OnnaisAuthError as error:
            raise UpdateFailed(AUTH_FAILED_MESSAGE) from error
        except OnnaisCommandError as error:
            raise UpdateFailed(str(error)) from error
        except Exception as error:  # noqa: BLE001 - never let a BLE failure escape a coordinator update
            raise UpdateFailed(f"BLE error talking to lock: {error}") from error
        self._force_refresh = False
        return data

    async def async_run_command(self, coro_factory) -> None:
        """Run one command session (which also reads status over the same
        connection) and publish that status.

        Waits behind any in-flight background read rather than failing.
        A second tap while a command is already queued is rejected, so
        mashing a button can't pile up minutes of queued connects.
        """
        if self._command_pending:
            raise HomeAssistantError("Still working on the previous command for this lock -- give it a moment.")
        self._command_pending = True
        try:
            status = await coro_factory()
        except OnnaisAuthError as error:
            self._force_refresh = True
            raise HomeAssistantError(AUTH_FAILED_MESSAGE) from error
        except OnnaisCommandError as error:
            # The lock may still have acted (a reply can be lost after it
            # moved), so the next health-check tick verifies real state.
            self._force_refresh = True
            raise HomeAssistantError(f"Lock didn't respond: {error}") from error
        except Exception as error:  # noqa: BLE001
            self._force_refresh = True
            raise HomeAssistantError(f"Bluetooth error talking to lock: {error}") from error
        finally:
            self._command_pending = False

        if status is not None:
            self._force_refresh = False
            self.async_set_updated_data(status)
        else:
            # Command succeeded but the same-session status read didn't.
            self._force_refresh = True
