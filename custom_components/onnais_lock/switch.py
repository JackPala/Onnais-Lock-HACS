"""ONNAIS buzzer and keypad-light switches."""

from __future__ import annotations

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
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
        OnnaisBuzzerSwitch(coordinator, entry),
        OnnaisKeypadLightSwitch(coordinator, entry),
    ])


class _OnnaisSwitchBase(OnnaisEntity, SwitchEntity):
    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: OnnaisCoordinator, entry: ConfigEntry, data_key: str) -> None:
        super().__init__(coordinator)
        self._data_key = data_key
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, coordinator.address)})

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._data_key)


class OnnaisBuzzerSwitch(_OnnaisSwitchBase):
    _attr_name = "Buzzer"
    _attr_icon = "mdi:volume-high"

    def __init__(self, coordinator: OnnaisCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "buzzer")
        self._attr_unique_id = f"{coordinator.address}_buzzer"

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_run_command(lambda: self.coordinator.client.set_buzzer(True))

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_run_command(lambda: self.coordinator.client.set_buzzer(False))


class OnnaisKeypadLightSwitch(_OnnaisSwitchBase):
    _attr_name = "Keypad light"
    _attr_icon = "mdi:keyboard-outline"

    def __init__(self, coordinator: OnnaisCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "keypad_light")
        self._attr_unique_id = f"{coordinator.address}_keypad_light"

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_run_command(lambda: self.coordinator.client.set_keypad_light(True))

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_run_command(lambda: self.coordinator.client.set_keypad_light(False))
