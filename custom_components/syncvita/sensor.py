"""Sensors of the Syncvita integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .entity import device_info

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
    from homeassistant.helpers.typing import StateType

    from .models import SyncvitaConfigEntry, SyncvitaRuntimeData

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SyncvitaSenderSensorDescription(SensorEntityDescription):
    """Describes a diagnostic sensor of the sending part."""

    value_fn: Callable[[SyncvitaRuntimeData], StateType | datetime]
    attrs_fn: Callable[[SyncvitaRuntimeData], dict[str, Any]] | None = None


def _queue_attributes(data: SyncvitaRuntimeData) -> dict[str, Any]:
    moments = [entry["measured_at"] for entry in data.store.queue]
    return {"oldest": min(moments) if moments else None}


SENDER_SENSORS: tuple[SyncvitaSenderSensorDescription, ...] = (
    SyncvitaSenderSensorDescription(
        key="last_transfer",
        translation_key="last_transfer",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.store.last_success,
    ),
    SyncvitaSenderSensorDescription(
        key="sent_values",
        translation_key="sent_values",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.store.sent_count,
    ),
    SyncvitaSenderSensorDescription(
        key="queue",
        translation_key="queue",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: len(data.store.queue),
        attrs_fn=_queue_attributes,
    ),
    SyncvitaSenderSensorDescription(
        key="last_error",
        translation_key="last_error",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.sender.last_error,
        attrs_fn=lambda data: {"http_status": data.sender.last_error_status},
    ),
    SyncvitaSenderSensorDescription(
        key="rate_limit_remaining",
        translation_key="rate_limit_remaining",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.client.rate_limit.remaining,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SyncvitaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    async_add_entities(
        SyncvitaSenderSensor(entry, description) for description in SENDER_SENSORS
    )


class SyncvitaSenderSensor(SensorEntity):
    """Diagnostic sensor updated by the sender via dispatcher."""

    entity_description: SyncvitaSenderSensorDescription
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, entry: SyncvitaConfigEntry, description: SyncvitaSenderSensorDescription
    ) -> None:
        """Initialize the sensor."""
        self.entity_description = description
        self._data = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Update on every sender change."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self._data.sender.signal, self.async_write_ha_state
            )
        )

    @property
    def native_value(self) -> StateType | datetime:
        """Return the current value."""
        return self.entity_description.value_fn(self._data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self._data)
