"""ONNAIS battery and programmed-slot sensors."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import OnnaisCoordinator
from .entity import OnnaisEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: OnnaisCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        OnnaisBatterySensor(coordinator, entry),
        OnnaisProgrammedSlotsSensor(coordinator, entry),
    ])


class _OnnaisSensorBase(OnnaisEntity, SensorEntity):

    def __init__(self, coordinator: OnnaisCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, coordinator.address)})


class OnnaisBatterySensor(_OnnaisSensorBase):
    _attr_name = "Battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: OnnaisCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{coordinator.address}_battery"

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("battery")


class OnnaisProgrammedSlotsSensor(_OnnaisSensorBase):
    """How many temp passcodes / fingerprints / key fobs are currently
    programmed into the lock, with the exact slot numbers as attributes --
    useful for noticing a code was added you didn't expect."""

    _attr_name = "Programmed codes"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:dialpad"

    def __init__(self, coordinator: OnnaisCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{coordinator.address}_programmed_slots"

    @property
    def native_value(self):
        data = self.coordinator.data or {}
        return (
            len(data.get("temp_passwords", []))
            + len(data.get("fingerprints", []))
            + len(data.get("key_fobs", []))
        )

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        return {
            "temp_password_slots": data.get("temp_passwords", []),
            "fingerprint_slots": data.get("fingerprints", []),
            "key_fob_slots": data.get("key_fobs", []),
        }
