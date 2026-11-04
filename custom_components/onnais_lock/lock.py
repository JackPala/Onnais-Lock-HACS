"""ONNAIS lock entity."""

from __future__ import annotations

from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OnnaisCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: OnnaisCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([OnnaisLock(coordinator, entry)])


class OnnaisLock(CoordinatorEntity[OnnaisCoordinator], LockEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = LockEntityFeature(0)

    def __init__(self, coordinator: OnnaisCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{coordinator.address}_lock"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name=entry.title,
            manufacturer="ONNAIS",
            model="Guard Smart RV Door Lock",
        )

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
        }

    async def async_lock(self, **kwargs) -> None:
        await self.coordinator.async_run_command(lambda: self.coordinator.client.set_locked(True))

    async def async_unlock(self, **kwargs) -> None:
        await self.coordinator.async_run_command(lambda: self.coordinator.client.set_locked(False))
