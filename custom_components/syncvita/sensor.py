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
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import dt as dt_util

from .const import (
    CONF_SLEEP_ENABLED,
    CONF_WORKOUTS_ENABLED,
    DEFAULT_ENABLED_METRICS,
    KNOWN_METRICS,
    STALE_AFTER,
    export_unit,
)
from .entity import SyncvitaExportEntity, device_info

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
    from homeassistant.helpers.typing import StateType

    from .coordinator import ExportData, MetricInfo, SyncvitaExportCoordinator
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
    coordinator = entry.runtime_data.coordinator
    if coordinator is None:
        return
    entities: list[SensorEntity] = [
        SyncvitaLastUploadSensor(coordinator),
        SyncvitaStoredValuesSensor(coordinator),
    ]
    if entry.options.get(CONF_SLEEP_ENABLED, True):
        entities.append(SyncvitaSleepSensor(coordinator))
    if entry.options.get(CONF_WORKOUTS_ENABLED, True):
        entities += [
            SyncvitaLastWorkoutSensor(coordinator),
            SyncvitaWorkoutsTodaySensor(coordinator),
        ]
    async_add_entities(entities)

    known: set[str] = set()

    @callback
    def _async_add_metric_sensors() -> None:
        # data is None until the first successful refresh.
        data: ExportData | None = coordinator.data
        if data is None:
            return
        new = [
            info
            for info in data.metrics.values()
            if info.has_sensor and info.metric not in known
        ]
        known.update(info.metric for info in new)
        if new:
            async_add_entities(SyncvitaMetricSensor(coordinator, info) for info in new)

    _async_add_metric_sensors()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_metric_sensors))


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


class SyncvitaLastUploadSensor(SyncvitaExportEntity, SensorEntity):
    """Time of the last upload from the iPhone app."""

    _attr_translation_key = "last_upload"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _unrecorded_attributes = frozenset({"age_minutes"})

    def __init__(self, coordinator: SyncvitaExportCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "last_upload")

    @property
    def native_value(self) -> datetime | None:
        """Return the upload time."""
        return self.export.last_upload

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the age in minutes."""
        last = self.export.last_upload
        if last is None:
            return {}
        return {"age_minutes": int((dt_util.utcnow() - last).total_seconds() // 60)}


class SyncvitaStoredValuesSensor(SyncvitaExportEntity, SensorEntity):
    """Number of stored export values over all types."""

    _attr_translation_key = "stored_values"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _unrecorded_attributes = frozenset({"counts"})

    def __init__(self, coordinator: SyncvitaExportCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "stored_values")

    @property
    def native_value(self) -> int:
        """Return the total number of values."""
        return sum(info.count for info in self.export.metrics.values())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the number of values per type."""
        return {
            "counts": {name: info.count for name, info in self.export.metrics.items()}
        }


class SyncvitaMetricSensor(SyncvitaExportEntity, SensorEntity):
    """Latest value (or today's total) of one export type."""

    _unrecorded_attributes = frozenset({"count", "first_date", "last_date"})

    def __init__(
        self, coordinator: SyncvitaExportCoordinator, info: MetricInfo
    ) -> None:
        """Initialize the sensor from the first seen metric info."""
        super().__init__(coordinator, f"metric_{info.metric}")
        self._metric = info.metric
        self._cumulative = info.cumulative
        mapping = export_unit(info.unit)
        self._attr_native_unit_of_measurement = mapping.unit
        self._attr_device_class = mapping.device_class
        self._attr_state_class = (
            SensorStateClass.TOTAL_INCREASING
            if info.cumulative
            else SensorStateClass.MEASUREMENT
        )
        self._attr_entity_registry_enabled_default = (
            info.metric in DEFAULT_ENABLED_METRICS
        )
        if info.metric in KNOWN_METRICS:
            self._attr_translation_key = info.metric
        else:
            self._attr_name = info.metric.replace("_", " ").capitalize()

    @property
    def _info(self) -> MetricInfo | None:
        return self.export.metrics.get(self._metric)

    @property
    def available(self) -> bool:
        """Unavailable while the type is missing from the export."""
        return super().available and self._info is not None

    @property
    def native_value(self) -> float | None:
        """Return the latest value; for cumulative types the total of today."""
        info = self._info
        if info is None:
            return None
        if self._cumulative and info.latest_date != dt_util.now().date().isoformat():
            return 0
        return info.latest_value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return measurement time, counts and the stale flag."""
        info = self._info
        if info is None:
            return {}
        latest_at = info.latest_at()
        return {
            "measured_at": latest_at.isoformat() if latest_at else None,
            "count": info.count,
            "first_date": info.first_date,
            "last_date": info.last_date,
            "stale": latest_at is None or dt_util.now() - latest_at > STALE_AFTER,
        }


class SyncvitaSleepSensor(SyncvitaExportEntity, SensorEntity):
    """Sleep duration of the last night."""

    _attr_translation_key = "sleep_last_night"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: SyncvitaExportCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "sleep_last_night")

    @property
    def native_value(self) -> float | None:
        """Return the sleep duration in hours."""
        night = self.export.sleep
        return night.duration_hours if night else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return phases, bedtime and wake time."""
        night = self.export.sleep
        if night is None:
            return {}
        return {
            "night": night.night,
            "bedtime": night.bedtime,
            "wake_time": night.wake_time,
            "stages_minutes": night.stages,
        }


class SyncvitaLastWorkoutSensor(SyncvitaExportEntity, SensorEntity):
    """Activity type of the newest workout."""

    _attr_translation_key = "last_workout"

    def __init__(self, coordinator: SyncvitaExportCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "last_workout")

    @property
    def native_value(self) -> str | None:
        """Return the activity type."""
        workouts = self.export.workouts
        return workouts[0].activity_type if workouts else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return start, duration, energy, distance and heart rate."""
        if not self.export.workouts:
            return {}
        workout = self.export.workouts[0]
        return {
            "start": workout.start.isoformat(),
            "end": workout.end.isoformat(),
            "duration_minutes": workout.duration_minutes,
            "energy_kcal": workout.energy_kcal,
            "distance_m": workout.distance_m,
            "avg_heart_rate": workout.avg_heart_rate,
            "max_heart_rate": workout.max_heart_rate,
        }


class SyncvitaWorkoutsTodaySensor(SyncvitaExportEntity, SensorEntity):
    """Number of workouts started today."""

    _attr_translation_key = "workouts_today"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator: SyncvitaExportCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "workouts_today")

    @property
    def native_value(self) -> int:
        """Return the number of today's workouts."""
        today = dt_util.now().date()
        return sum(
            1
            for workout in self.export.workouts
            if dt_util.as_local(workout.start).date() == today
        )
