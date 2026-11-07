"""ONNAIS lock entity."""

from __future__ import annotations

from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import OnnaisCoordinator
from .entity import OnnaisEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: OnnaisCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([OnnaisLock(coordinator, entry)])


class OnnaisLock(OnnaisEntity, LockEntity):
    _attr_name = None
    _attr_supported_features = LockEntityFeature(0)

    def __init__(self, coordinator: OnnaisCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_lock"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name=entry.title,
            manufacturer="ONNAIS",
            model="Guard Smart RV Door Lock",
        )
        self._attr_is_locking = False
        self._attr_is_unlocking = False

    @property
    def is_locked(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("locked")

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        return {
            "temp_passwords_in_use": data.get("temp_passwords", []),
            "fingerprints_in_use": data.get("fingerprints", []),
            "key_fobs_in_use": data.get("key_fobs", []),
            "last_status_read_ok": self.coordinator.last_update_success,
        }

    async def async_lock(self, **kwargs) -> None:
        await self._run(True)

    async def async_unlock(self, **kwargs) -> None:
        await self._run(False)

    async def _run(self, locked: bool) -> None:
        # Show "Locking…"/"Unlocking…" while the connect (which can take
        # tens of seconds on this link) is in progress.
        self._attr_is_locking = locked
        self._attr_is_unlocking = not locked
        self.async_write_ha_state()
        try:
            await self.coordinator.async_run_command(lambda: self.coordinator.client.set_locked(locked))
        finally:
            self._attr_is_locking = False
            self._attr_is_unlocking = False
            self.async_write_ha_state()
