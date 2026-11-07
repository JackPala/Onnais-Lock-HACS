"""Shared base entity for ONNAIS lock entities."""

from __future__ import annotations

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import OnnaisCoordinator


class OnnaisEntity(CoordinatorEntity[OnnaisCoordinator]):
    """Stays available whether or not the last status read succeeded.

    This lock sits on a weak, sparse-advertising BLE link, so individual
    status reads fail routinely. If that greyed the entity out (the
    CoordinatorEntity default), Home Assistant would also disable the
    lock/unlock controls -- leaving no way to retry until a background
    check happened to succeed. Instead entities keep showing the last
    known state, and every command connects on demand and reports its own
    success or failure.
    """

    _attr_has_entity_name = True

    @property
    def available(self) -> bool:
        return True
