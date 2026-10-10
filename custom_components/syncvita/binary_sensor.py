"""Binary sensor of the Syncvita integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import BinarySensorEntity

from .entity import SyncvitaExportEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import SyncvitaExportCoordinator
    from .models import SyncvitaConfigEntry

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SyncvitaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensor."""
    if (coordinator := entry.runtime_data.coordinator) is not None:
        async_add_entities([SyncvitaExportActiveBinarySensor(coordinator)])


class SyncvitaExportActiveBinarySensor(SyncvitaExportEntity, BinarySensorEntity):
    """On while the person shares the Apple Health export."""

    _attr_translation_key = "export_active"

    def __init__(self, coordinator: SyncvitaExportCoordinator) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, "export_active")

    @property
    def available(self) -> bool:
        """Stay available while the export is disabled; that is the state."""
        return (
            self.coordinator.last_update_success and self.coordinator.data is not None
        )

    @property
    def is_on(self) -> bool:
        """Return True if the export is enabled."""
        return self.export.enabled

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the released types."""
        return {"released_types": self.export.released_types}
