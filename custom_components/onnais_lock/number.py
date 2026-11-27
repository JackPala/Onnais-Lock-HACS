"""ONNAIS polling-interval number entity (device page -> Configuration)."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_POLL_INTERVAL, DOMAIN, MAX_POLL_INTERVAL, MIN_POLL_INTERVAL
from .coordinator import OnnaisCoordinator
from .entity import OnnaisEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: OnnaisCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([OnnaisPollIntervalNumber(coordinator, entry)])


class OnnaisPollIntervalNumber(OnnaisEntity, NumberEntity):
    """Seconds between status reads. 0 = no routine polling (default).

    Backed by the config entry's options, so it always matches the
    integration's Configure dialog.
    """

    _attr_name = "Polling interval"
    _attr_icon = "mdi:timer-refresh-outline"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_POLL_INTERVAL
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS

    def __init__(self, coordinator: OnnaisCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{coordinator.address}_poll_interval"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, coordinator.address)})

    @property
    def native_value(self) -> float:
        return self.coordinator.poll_interval

    async def async_set_native_value(self, value: float) -> None:
        interval = int(value)
        if 0 < interval < MIN_POLL_INTERVAL:
            raise ServiceValidationError(
                f"Use 0 to turn polling off, or at least {MIN_POLL_INTERVAL} seconds."
            )
        # The options update listener applies this live (see __init__.py).
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, CONF_POLL_INTERVAL: interval}
        )
