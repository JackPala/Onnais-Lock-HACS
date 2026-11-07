"""The ONNAIS RV Lock integration."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .const import (
    ATTR_PASSCODE,
    ATTR_SLOT,
    CONF_SECRET,
    DOMAIN,
    SERVICE_BEGIN_ADD_KEY_FOB,
    SERVICE_CANCEL_OPERATION,
    SERVICE_DELETE_FINGERPRINT,
    SERVICE_DELETE_KEY_FOB,
    SERVICE_DELETE_TEMP_PASSWORD,
    SERVICE_SET_TEMP_PASSWORD,
    SERVICE_UNBIND,
)
from .coordinator import OnnaisCoordinator

PLATFORMS = ["lock", "sensor", "switch"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    secret = bytes.fromhex(entry.data[CONF_SECRET])
    coordinator = OnnaisCoordinator(hass, entry, entry.data["address"], secret)
    # Initial status read runs in the background: it can take tens of
    # seconds on this link (or fail), and must neither block Home
    # Assistant's startup nor fail setup (ConfigEntryNotReady would make HA
    # retry setup in a tight loop, each retry a fresh connect + chirp).
    # Entities start with unknown state and fill in when it completes.
    entry.async_create_background_task(
        hass, coordinator.async_refresh(), name=f"onnais_lock initial refresh {entry.data['address']}"
    )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_SET_TEMP_PASSWORD):
        return  # already registered by an earlier config entry

    async def _coordinator_for_call(call: ServiceCall) -> OnnaisCoordinator:
        coordinators: dict = hass.data.get(DOMAIN, {})
        entity_ids = call.data.get("entity_id") or []
        if entity_ids:
            registry = er.async_get(hass)
            for entity_id in entity_ids:
                entry_ = registry.async_get(entity_id)
                if entry_ is not None and entry_.config_entry_id in coordinators:
                    return coordinators[entry_.config_entry_id]
            raise HomeAssistantError(f"None of {entity_ids} belong to an ONNAIS lock")
        if len(coordinators) == 1:
            return next(iter(coordinators.values()))
        raise HomeAssistantError("More than one ONNAIS lock is set up -- pick the target lock entity")

    async def set_temp_password(call: ServiceCall) -> None:
        coordinator = await _coordinator_for_call(call)
        await coordinator.async_run_command(
            lambda: coordinator.client.set_temp_password(call.data[ATTR_SLOT], call.data[ATTR_PASSCODE])
        )

    async def delete_temp_password(call: ServiceCall) -> None:
        coordinator = await _coordinator_for_call(call)
        await coordinator.async_run_command(lambda: coordinator.client.delete_temp_password(call.data[ATTR_SLOT]))

    async def delete_fingerprint(call: ServiceCall) -> None:
        coordinator = await _coordinator_for_call(call)
        await coordinator.async_run_command(lambda: coordinator.client.delete_fingerprint(call.data[ATTR_SLOT]))

    async def begin_add_key_fob(call: ServiceCall) -> None:
        coordinator = await _coordinator_for_call(call)
        await coordinator.async_run_command(coordinator.client.begin_add_key_fob)

    async def delete_key_fob(call: ServiceCall) -> None:
        coordinator = await _coordinator_for_call(call)
        await coordinator.async_run_command(lambda: coordinator.client.delete_key_fob(call.data[ATTR_SLOT]))

    async def cancel_operation(call: ServiceCall) -> None:
        coordinator = await _coordinator_for_call(call)
        await coordinator.async_run_command(coordinator.client.cancel_operation)

    async def unbind(call: ServiceCall) -> None:
        if not call.data.get("confirm"):
            raise HomeAssistantError(
                "unbind is destructive (factory-resets the lock's owner secret). "
                "Call again with confirm: true to proceed."
            )
        coordinator = await _coordinator_for_call(call)
        await coordinator.async_run_command(coordinator.client.unbind)

    hass.services.async_register(
        DOMAIN, SERVICE_SET_TEMP_PASSWORD, set_temp_password,
        schema=vol.Schema({
            vol.Optional("entity_id"): cv.entity_ids,
            vol.Required(ATTR_SLOT): vol.All(int, vol.Range(min=0, max=9)),
            vol.Required(ATTR_PASSCODE): cv.string,
        }),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_DELETE_TEMP_PASSWORD, delete_temp_password,
        schema=vol.Schema({
            vol.Optional("entity_id"): cv.entity_ids,
            vol.Required(ATTR_SLOT): vol.All(int, vol.Range(min=0, max=9)),
        }),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_DELETE_FINGERPRINT, delete_fingerprint,
        schema=vol.Schema({
            vol.Optional("entity_id"): cv.entity_ids,
            vol.Required(ATTR_SLOT): vol.All(int, vol.Range(min=0, max=255)),
        }),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_BEGIN_ADD_KEY_FOB, begin_add_key_fob,
        schema=vol.Schema({vol.Optional("entity_id"): cv.entity_ids}),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_DELETE_KEY_FOB, delete_key_fob,
        schema=vol.Schema({
            vol.Optional("entity_id"): cv.entity_ids,
            vol.Required(ATTR_SLOT): vol.All(int, vol.Range(min=0, max=255)),
        }),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CANCEL_OPERATION, cancel_operation,
        schema=vol.Schema({vol.Optional("entity_id"): cv.entity_ids}),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_UNBIND, unbind,
        schema=vol.Schema({
            vol.Optional("entity_id"): cv.entity_ids,
            vol.Required("confirm"): cv.boolean,
        }),
    )
