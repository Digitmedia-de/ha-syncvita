"""Constants for the Syncvita integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import (
    PERCENTAGE,
    UnitOfBloodGlucoseConcentration,
    UnitOfEnergy,
    UnitOfLength,
    UnitOfMass,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
)

DOMAIN: Final = "syncvita"

API_BASE_URL: Final = "https://api.syncvita.de/v1"
API_TIMEOUT_SECONDS: Final = 20
EXPORT_API_VERSION: Final = "1.6"

CONF_SENSORS: Final = "sensors"
CONF_NOTIFY_ERRORS: Final = "notify_errors"
CONF_MAX_AGE_HOURS: Final = "max_age_hours"
CONF_READ_ENABLED: Final = "read_enabled"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_SLEEP_ENABLED: Final = "sleep_enabled"
CONF_WORKOUTS_ENABLED: Final = "workouts_enabled"

DEFAULT_NOTIFY_ERRORS: Final = True
DEFAULT_MAX_AGE_HOURS: Final = 72
DEFAULT_SCAN_INTERVAL: Final = 15
MIN_SCAN_INTERVAL: Final = 10
MAX_SCAN_INTERVAL: Final = 60
MAX_MAX_AGE_HOURS: Final = 720

SEND_PAUSE_BELOW: Final = 10
READ_PAUSE_BELOW: Final = 30
BULK_CHUNK_SIZE: Final = 100
RETRY_DELAYS_MINUTES: Final = (1, 2, 5, 15, 30, 60)
SENT_KEYS_PER_SOURCE: Final = 500
SEEN_WORKOUTS_MAX: Final = 200

FULL_REFRESH_INTERVAL: Final = timedelta(hours=1)
STALE_AFTER: Final = timedelta(hours=48)
WORKOUT_LOOKBACK_DAYS: Final = 7

METRIC_SLEEP: Final = "sleep_analysis"
METRIC_WORKOUTS: Final = "workouts"
EVENT_WORKOUT_FINISHED: Final = "syncvita_workout_finished"
EVENT_SLEEP_RECORDED: Final = "syncvita_sleep_recorded"
SIGNAL_SENDER_UPDATED: Final = "syncvita_sender_updated_{}"


@dataclass(frozen=True, slots=True)
class UnitMapping:
    """Home Assistant unit and device class for a unit of the export API."""

    unit: str | None
    device_class: SensorDeviceClass | None = None


EXPORT_UNITS: Final[dict[str, UnitMapping]] = {
    "count": UnitMapping(None),
    "count/min": UnitMapping("bpm"),
    "%": UnitMapping(PERCENTAGE),
    "kg": UnitMapping(UnitOfMass.KILOGRAMS, SensorDeviceClass.WEIGHT),
    "g": UnitMapping(UnitOfMass.GRAMS, SensorDeviceClass.WEIGHT),
    "mg": UnitMapping(UnitOfMass.MILLIGRAMS, SensorDeviceClass.WEIGHT),
    "cm": UnitMapping(UnitOfLength.CENTIMETERS, SensorDeviceClass.DISTANCE),
    "m": UnitMapping(UnitOfLength.METERS, SensorDeviceClass.DISTANCE),
    "m/s": UnitMapping(UnitOfSpeed.METERS_PER_SECOND, SensorDeviceClass.SPEED),
    "ms": UnitMapping(UnitOfTime.MILLISECONDS, SensorDeviceClass.DURATION),
    "min": UnitMapping(UnitOfTime.MINUTES, SensorDeviceClass.DURATION),
    "degC": UnitMapping(UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
    "kcal": UnitMapping(UnitOfEnergy.KILO_CALORIE, SensorDeviceClass.ENERGY),
    "mL": UnitMapping(UnitOfVolume.MILLILITERS, SensorDeviceClass.VOLUME),
    "mg/dL": UnitMapping(
        UnitOfBloodGlucoseConcentration.MILLIGRAMS_PER_DECILITER,
        SensorDeviceClass.BLOOD_GLUCOSE_CONCENTRATION,
    ),
}


def export_unit(api_unit: str | None) -> UnitMapping:
    """Translate an export unit; unknown units are passed through unchanged."""
    if api_unit is None:
        return UnitMapping(None)
    return EXPORT_UNITS.get(api_unit, UnitMapping(api_unit))


KNOWN_METRICS: Final = frozenset(
    {
        "active_energy_burned",
        "basal_energy_burned",
        "blood_glucose",
        "body_fat_percentage",
        "body_mass",
        "body_mass_index",
        "body_temperature",
        "dietary_caffeine",
        "dietary_carbohydrates",
        "dietary_energy",
        "dietary_fat_total",
        "dietary_protein",
        "dietary_water",
        "distance_walking_running",
        "exercise_time",
        "flights_climbed",
        "heart_rate",
        "heart_rate_recovery",
        "heart_rate_variability_sdnn",
        "height",
        "oxygen_saturation",
        "respiratory_rate",
        "resting_heart_rate",
        "stand_time",
        "step_count",
        "time_in_daylight",
        "vo2_max",
        "walking_heart_rate_average",
        "walking_speed",
        "walking_steadiness",
        "wrist_temperature",
    }
)

DEFAULT_ENABLED_METRICS: Final = frozenset(
    {
        "heart_rate_variability_sdnn",
        "oxygen_saturation",
        "respiratory_rate",
        "resting_heart_rate",
        "step_count",
        "vo2_max",
    }
)
