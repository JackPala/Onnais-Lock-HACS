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


HEALTH_CHECK_INTERVAL = timedelta(minutes=5)
# This is NOT a routine poll -- see _async_update_data's gating logic. It
# exists for exactly one reason: without it, a lock that fails its one and
# only startup connection (easily possible with this lock's weak signal)
# would stay "unavailable" forever, with no periodic timer to ever retry
# and no way for the user to force one either, since Home Assistant
# disables the lock/unlock buttons on an unavailable entity -- observed
# live as both locks being completely stuck, uncontrollable, for two full
# days. While everything is healthy, ticks on this interval are skipped
# entirely (no connection, no chirp); they only do real work -- and retry
# every five minutes until they succeed -- while the lock is in a known-bad
# state. A successful recovery beeps once, same as any other connection;
# that's an acceptable, rare price for "doesn't get stranded for days."


class OnnaisCoordinator(DataUpdateCoordinator[dict]):
    """Fetches lock/battery/buzzer/keypad-light/code status, and is also
    the shared place entities and services reach the BLE client through, so
    every connection goes through one coordinated session at a time rather
    than racing concurrent BLE connects to the same radio.

    Status refreshes at startup, immediately after every lock/unlock or
    service call (``async_run_command`` below), and -- only while in a
    failed state -- on HEALTH_CHECK_INTERVAL until it recovers. A healthy
    lock makes zero background connections and stays silent.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, address: str, secret: bytes) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"onnais_lock_{address}",
            update_interval=HEALTH_CHECK_INTERVAL,
        )
        self.address = address
        self.entry = entry
        # Lets the very first _async_update_data call run for real even
        # though last_update_success defaults to True before any update
        # has ever run (which would otherwise make the gate below skip it).
        self._force_refresh = True
        ble_device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
        if ble_device is None:
            raise ConfigEntryNotReady(f"ONNAIS lock {address} not currently visible to Home Assistant's Bluetooth stack")
        self.client = OnnaisLockClient(ble_device, secret)

    def _refresh_ble_device(self) -> None:
        ble_device = bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)
        if ble_device is not None:
            self.client.update_ble_device(ble_device)

    async def _async_update_data(self) -> dict:
        # Skip a routine HEALTH_CHECK_INTERVAL tick while everything is
        # already known to be working -- only a forced refresh (an actual
        # lock/unlock/service call) or recovering from a failure actually
        # connects. See HEALTH_CHECK_INTERVAL's comment for why.
        if self.last_update_success and not self._force_refresh and self.data is not None:
            return self.data
        self._force_refresh = False

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
        self._force_refresh = True
        self._refresh_ble_device()
        try:
            await coro_factory()
        finally:
            await self.async_request_refresh()
