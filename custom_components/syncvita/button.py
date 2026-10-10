"""Button of the Syncvita integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory

from .entity import device_info

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .models import SyncvitaConfigEntry

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SyncvitaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the button."""
    async_add_entities([SyncvitaProcessQueueButton(entry)])


class SyncvitaProcessQueueButton(ButtonEntity):
    """Send the queue now instead of waiting for the next retry."""

    _attr_has_entity_name = True
    _attr_translation_key = "process_queue"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: SyncvitaConfigEntry) -> None:
        """Initialize the button."""
        self._sender = entry.runtime_data.sender
        self._attr_unique_id = f"{entry.entry_id}_process_queue"
        self._attr_device_info = device_info(entry)

    async def async_press(self) -> None:
        """Process the queue."""
        await self._sender.async_process_queue()
