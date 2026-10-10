"""Shared entity helpers of the Syncvita integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ExportData, SyncvitaExportCoordinator

if TYPE_CHECKING:
    from .models import SyncvitaConfigEntry


def device_info(entry: SyncvitaConfigEntry) -> DeviceInfo:
    """Return the service device of one person."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"Syncvita {entry.title}",
        manufacturer="Syncvita",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url="https://syncvita.de",
    )


class SyncvitaExportEntity(CoordinatorEntity[SyncvitaExportCoordinator]):
    """Base entity of the export part."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SyncvitaExportCoordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = device_info(entry)

    @property
    def export(self) -> ExportData:
        """Return the current export data, or a disabled placeholder."""
        return self.coordinator.data or ExportData(enabled=False)

    @property
    def available(self) -> bool:
        """Value entities are unavailable while the export is disabled."""
        return super().available and self.export.enabled
