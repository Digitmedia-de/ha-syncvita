"""The parsers must accept the recorded API 1.6 responses."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from custom_components.syncvita.coordinator import (
    MetricInfo,
    SleepNight,
    Workout,
    parse_api_timestamp,
)

REAL = Path(__file__).parent / "fixtures" / "real"


def _real(name: str) -> dict[str, Any]:
    recording = json.loads((REAL / f"{name}.json").read_text(encoding="utf-8"))
    assert recording["status"] == (401 if name == "unauthorized" else 200), name
    return recording["body"]


def test_metrics() -> None:
    data = _real("health_export_metrics")["data"]
    metrics = [MetricInfo.from_api(raw) for raw in data["metrics"]]
    assert metrics
    assert any(metric.has_sensor for metric in metrics)
    if data.get("last_upload_at"):
        assert parse_api_timestamp(data["last_upload_at"]) is not None


def test_settings() -> None:
    data = _real("health_export_settings")["data"]
    assert isinstance(data["enabled"], bool)
    assert isinstance(data["types"], list)


def test_sleep_nights() -> None:
    nights = _real("health_export_daily_sleep")["data"]["nights"]
    for raw in nights:
        night = SleepNight.from_api(raw)
        assert night.duration_hours >= 0


def test_workouts() -> None:
    workouts = _real("health_export_daily_workouts")["data"]["workouts"]
    for raw in workouts:
        workout = Workout.from_api(raw)
        assert workout.end >= workout.start


def test_unauthorized() -> None:
    assert _real("unauthorized")["error"]["code"] == "INVALID_API_KEY"
