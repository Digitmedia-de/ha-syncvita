"""Tests for conversion, rounding and the state guards."""

from __future__ import annotations

from datetime import UTC, datetime

from homeassistant.core import State
import pytest

from custom_components.syncvita.measures import (
    Measure,
    OutOfRangeError,
    UnsupportedUnitError,
    build_measurement,
    measurement_from_states,
)

pytestmark = pytest.mark.usefixtures("berlin")

MORNING = datetime(2026, 10, 10, 5, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("measure", "value", "unit", "expected"),
    [
        (Measure.WEIGHT, 81.234, "kg", {"weight": 81.23}),
        (Measure.WEIGHT, 81234, "g", {"weight": 81.23}),
        (Measure.WEIGHT, 180.5, "lb", {"weight": 180.5, "unit": "lbs"}),
        (Measure.WEIGHT, 180.5, "lbs", {"weight": 180.5, "unit": "lbs"}),
        (Measure.BODY_FAT, 20.456, "%", {"percentage": 20.46}),
        (Measure.HEART_RATE, 61.6, "bpm", {"bpm": 62}),
        (Measure.BLOOD_OXYGEN, 97.254, "%", {"spo2": 97.25}),
        (Measure.BLOOD_SUGAR, 5.5, "mmol/L", {"glucose": 99}),
        (Measure.BLOOD_SUGAR, 101.4, "mg/dL", {"glucose": 101}),
        (Measure.HYDRATION, 0.25, "L", {"milliliters": 250}),
        (Measure.HYDRATION, 330, "mL", {"milliliters": 330}),
    ],
)
async def test_build_measurement_converts(
    measure: Measure, value: float, unit: str, expected: dict[str, float | str]
) -> None:
    measurement = build_measurement(measure, value, unit, MORNING)
    assert measurement.values == expected
    assert (measurement.date, measurement.time) == ("2026-10-10", "07:30:00")
    assert measurement.body() == {**expected, "date": "2026-10-10", "time": "07:30:00"}


async def test_integer_fields_are_int() -> None:
    measurement = build_measurement(Measure.HEART_RATE, 61.6, "bpm", MORNING)
    assert isinstance(measurement.values["bpm"], int)


async def test_key_contains_local_time_and_value() -> None:
    measurement = build_measurement(Measure.WEIGHT, 81.2, "kg", MORNING)
    assert measurement.key == "2026-10-10T07:30:00|81.2"


async def test_local_date_after_midnight() -> None:
    late = datetime(2026, 10, 10, 22, 30, tzinfo=UTC)
    measurement = build_measurement(Measure.WEIGHT, 80, "kg", late)
    assert (measurement.date, measurement.time) == ("2026-10-11", "00:30:00")


async def test_daylight_saving_end() -> None:
    after_switch = datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
    measurement = build_measurement(Measure.WEIGHT, 80, "kg", after_switch)
    assert (measurement.date, measurement.time) == ("2026-10-25", "02:30:00")


@pytest.mark.parametrize(
    ("measure", "value", "unit"),
    [
        (Measure.HEART_RATE, 350, "bpm"),
        (Measure.HEART_RATE, 10, "bpm"),
        (Measure.HYDRATION, 11, "L"),
        (Measure.BLOOD_SUGAR, 5, "mg/dL"),
        (Measure.BODY_FAT, -1, "%"),
    ],
)
async def test_out_of_range(measure: Measure, value: float, unit: str) -> None:
    with pytest.raises(OutOfRangeError):
        build_measurement(measure, value, unit, MORNING)


@pytest.mark.parametrize(
    ("measure", "unit"),
    [(Measure.WEIGHT, "st"), (Measure.BODY_FAT, None), (Measure.HYDRATION, "gal")],
)
async def test_unsupported_unit(measure: Measure, unit: str | None) -> None:
    with pytest.raises(UnsupportedUnitError):
        build_measurement(measure, 1, unit, MORNING)


def _state(value: str, unit: str = "kg") -> State:
    return State(
        "sensor.scale_weight",
        value,
        {"unit_of_measurement": unit},
        last_changed=MORNING,
    )


async def test_states_produce_measurement() -> None:
    measurement = measurement_from_states(
        Measure.WEIGHT, _state("80.0"), _state("81.2")
    )
    assert measurement is not None
    assert measurement.values == {"weight": 81.2}


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (None, "81.2"),
        ("unknown", "81.2"),
        ("unavailable", "81.2"),
        ("80.0", "unknown"),
        ("80.0", "unavailable"),
        ("80.0", "80.0"),
        ("80.0", "nan"),
        ("80.0", "inf"),
        ("80.0", ""),
        ("80.0", "80,5"),
        ("80.0", "abc"),
    ],
)
async def test_guards(old: str | None, new: str) -> None:
    old_state = _state(old) if old is not None else None
    assert measurement_from_states(Measure.WEIGHT, old_state, _state(new)) is None


async def test_guard_missing_new_state() -> None:
    assert measurement_from_states(Measure.WEIGHT, _state("80.0"), None) is None


async def test_guard_logs_unsupported_unit(caplog: pytest.LogCaptureFixture) -> None:
    result = measurement_from_states(
        Measure.WEIGHT, _state("80.0", "st"), _state("12.6", "st")
    )
    assert result is None
    assert "unsupported unit" in caplog.text


async def test_guard_logs_out_of_range(caplog: pytest.LogCaptureFixture) -> None:
    result = measurement_from_states(Measure.WEIGHT, _state("80.0"), _state("900"))
    assert result is None
    assert "outside the allowed range" in caplog.text
