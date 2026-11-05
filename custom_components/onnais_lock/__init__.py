"""The ONNAIS RV Lock integration."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

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
    # Deliberately async_refresh(), not async_config_entry_first_refresh().
    # The latter raises ConfigEntryNotReady on failure, which makes Home
    # Assistant retry this entire setup function every 5 seconds forever
    # while the lock is unreachable -- and each of those retries runs our
    # own multi-attempt connect logic (~2 minutes of hammering per cycle),
    # producing a near-continuous storm of connection attempts (and, since
    # this lock chirps on connect, audible beeping every 1-2 minutes) for
    # as long as it's out of range. A vehicle-mounted BLE lock being
    # temporarily out of range is an expected, ordinary condition, not a
    # setup failure: async_refresh() lets setup finish immediately either
    # way, with entities simply starting "unavailable" if the first fetch
    # fails. The periodic 10-minute reconcile (and any lock/unlock
    # attempt) still recovers it once it's back in range -- without ever
    # retrying in a tight loop.
    await coordinator.async_refresh()

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
        entity_registry_ids = call.data.get("entity_id", [])
        for coordinator in hass.data.get(DOMAIN, {}).values():
            for entity_id in entity_registry_ids:
                if coordinator.address in entity_id:
                    return coordinator
        # Single-lock installs: just use the only coordinator present.
        coordinators = list(hass.data.get(DOMAIN, {}).values())
        if len(coordinators) == 1:
            return coordinators[0]
        raise ValueError("Could not resolve which ONNAIS lock this service call targets")

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
            raise ValueError(
                "unbind is destructive (factory-resets the lock's owner secret). "
                "Call again with confirm: true to proceed."
            )
        coordinator = await _coordinator_for_call(call)
        await coordinator.client.unbind()

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
