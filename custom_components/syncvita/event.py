"""Event entities of the Syncvita integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.event import EventEntity, EventEntityDescription
from homeassistant.core import Event, HomeAssistant, callback

from .const import (
    CONF_SLEEP_ENABLED,
    CONF_WORKOUTS_ENABLED,
    EVENT_SLEEP_RECORDED,
    EVENT_WORKOUT_FINISHED,
)
from .entity import device_info

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .models import SyncvitaConfigEntry

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SyncvitaEventDescription(EventEntityDescription):
    """Maps a bus event of this integration to an event entity."""

    bus_event: str
    event_type: str
    option: str


EVENTS: tuple[SyncvitaEventDescription, ...] = (
    SyncvitaEventDescription(
        key="workout",
        translation_key="workout",
        event_types=["workout_finished"],
        event_type="workout_finished",
        bus_event=EVENT_WORKOUT_FINISHED,
        option=CONF_WORKOUTS_ENABLED,
    ),
    SyncvitaEventDescription(
        key="sleep",
        translation_key="sleep",
        event_types=["sleep_recorded"],
        event_type="sleep_recorded",
        bus_event=EVENT_SLEEP_RECORDED,
        option=CONF_SLEEP_ENABLED,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SyncvitaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the event entities."""
    if entry.runtime_data.coordinator is None:
        return
    async_add_entities(
        SyncvitaEvent(entry, description)
        for description in EVENTS
        if entry.options.get(description.option, True)
    )


class SyncvitaEvent(EventEntity):
    """Event entity mirroring syncvita_* bus events of one person."""

    entity_description: SyncvitaEventDescription
    _attr_has_entity_name = True

    def __init__(
        self, entry: SyncvitaConfigEntry, description: SyncvitaEventDescription
    ) -> None:
        """Initialize the event entity."""
        self.entity_description = description
        self._entry_id = entry.entry_id
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Listen to the bus event."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hass.bus.async_listen(
                self.entity_description.bus_event, self._async_handle_event
            )
        )

    @callback
    def _async_handle_event(self, event: Event) -> None:
        if event.data.get("config_entry_id") != self._entry_id:
            return
        self._trigger_event(
            self.entity_description.event_type,
            {k: v for k, v in event.data.items() if k != "config_entry_id"},
        )
        self.async_write_ha_state()
