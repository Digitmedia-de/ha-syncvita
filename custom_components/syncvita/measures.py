"""Measures sent to Syncvita: units, ranges, guards and request bodies."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import logging
import math
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from homeassistant.core import State

_LOGGER = logging.getLogger(__name__)

INVALID_STATES = (STATE_UNKNOWN, STATE_UNAVAILABLE)
POUND_UNITS = frozenset({"lb", "lbs"})
MMOL_TO_MG_DL = 18.0182


class Measure(StrEnum):
    """Measures supported by the sending part."""

    WEIGHT = "weight"
    BODY_FAT = "body_fat"
    HEART_RATE = "heart_rate"
    BLOOD_OXYGEN = "blood_oxygen"
    BLOOD_SUGAR = "blood_sugar"
    HYDRATION = "hydration"


@dataclass(frozen=True, slots=True)
class MeasureSpec:
    """API endpoint, field, unit factors and allowed range of a measure."""

    path: str
    bulk_type: str
    field: str
    base_unit: str
    minimum: float
    maximum: float
    decimals: int
    units: Mapping[str, float]
    device_class: SensorDeviceClass | None = None


MEASURES: dict[Measure, MeasureSpec] = {
    Measure.WEIGHT: MeasureSpec(
        "/weight",
        "weight",
        "weight",
        "kg",
        0,
        500,
        2,
        {"kg": 1.0, "g": 0.001, "lb": 1.0, "lbs": 1.0},
        SensorDeviceClass.WEIGHT,
    ),
    Measure.BODY_FAT: MeasureSpec(
        "/body-fat", "body-fat", "percentage", "%", 0, 100, 2, {"%": 1.0}
    ),
    Measure.HEART_RATE: MeasureSpec(
        "/heart-rate",
        "heart-rate",
        "bpm",
        "bpm",
        20,
        300,
        0,
        {"bpm": 1.0, "beats/min": 1.0},
    ),
    Measure.BLOOD_OXYGEN: MeasureSpec(
        "/blood-oxygen", "blood-oxygen", "spo2", "%", 0, 100, 2, {"%": 1.0}
    ),
    Measure.BLOOD_SUGAR: MeasureSpec(
        "/blood-sugar",
        "blood-sugar",
        "glucose",
        "mg/dL",
        10,
        600,
        0,
        {"mg/dL": 1.0, "mmol/L": MMOL_TO_MG_DL},
    ),
    Measure.HYDRATION: MeasureSpec(
        "/hydration",
        "hydration",
        "milliliters",
        "mL",
        0,
        10000,
        0,
        {"mL": 1.0, "L": 1000.0},
    ),
}


class UnsupportedUnitError(ValueError):
    """The unit cannot be converted for this measure."""


class OutOfRangeError(ValueError):
    """The converted value is outside the API limits."""


@dataclass(frozen=True, slots=True)
class Measurement:
    """One value ready to be sent, with local date and time."""

    measure: Measure
    measured_at: datetime
    values: dict[str, float | int | str]
    date: str
    time: str

    @property
    def key(self) -> str:
        """Return the deduplication key: local timestamp and value."""
        return f"{self.date}T{self.time}|{self.values[MEASURES[self.measure].field]}"

    def body(self) -> dict[str, Any]:
        """Return the body for the single-value endpoint."""
        return {**self.values, "date": self.date, "time": self.time}


def build_measurement(
    measure: Measure, value: float, unit: str | None, measured_at: datetime
) -> Measurement:
    """Convert, round and range-check a value."""
    spec = MEASURES[measure]
    if unit is None or unit not in spec.units:
        raise UnsupportedUnitError(f"{measure}: unsupported unit {unit!r}")
    converted = value * spec.units[unit]
    rounded: float | int = (
        round(converted, spec.decimals) if spec.decimals else round(converted)
    )
    if not spec.minimum <= rounded <= spec.maximum:
        raise OutOfRangeError(
            f"{measure}: {rounded} outside {spec.minimum}-{spec.maximum}"
        )
    values: dict[str, float | int | str] = {spec.field: rounded}
    if measure is Measure.WEIGHT and unit in POUND_UNITS:
        values["unit"] = "lbs"
    local = dt_util.as_local(measured_at)
    return Measurement(
        measure=measure,
        measured_at=measured_at,
        values=values,
        date=local.strftime("%Y-%m-%d"),
        time=local.strftime("%H:%M:%S"),
    )


def measurement_from_states(
    measure: Measure, old_state: State | None, new_state: State | None
) -> Measurement | None:
    """Return the measurement of a state change, or None if it must be skipped."""
    if old_state is None or new_state is None:
        return None
    if old_state.state in INVALID_STATES or new_state.state in INVALID_STATES:
        return None
    if old_state.state == new_state.state:
        return None
    try:
        value = float(new_state.state)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    unit = new_state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    try:
        return build_measurement(measure, value, unit, new_state.last_changed)
    except UnsupportedUnitError:
        _LOGGER.warning(
            "%s has an unsupported unit %r for %s, value not sent",
            new_state.entity_id,
            unit,
            measure,
        )
    except OutOfRangeError:
        _LOGGER.warning(
            "%s: value %s is outside the allowed range for %s, not sent",
            new_state.entity_id,
            new_state.state,
            measure,
        )
    return None
