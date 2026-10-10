"""Tests for the sensors of the values stored in Syncvita."""

from __future__ import annotations

from datetime import timedelta
import json
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN

from custom_components.syncvita.diagnostics import async_get_config_entry_diagnostics

from .conftest import API, calls_to, entity_id_for, mock_values_api, setup_integration

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from pytest_homeassistant_custom_component.test_util.aiohttp import (
        AiohttpClientMocker,
    )

NOW = "2026-10-10 10:00:00+00:00"  # 12:00 in Berlin

STATS: dict[str, Any] = {
    "steps": {"pending": 0, "synced": 3, "total": 3, "latest": 70},
    "weight": {"pending": 0, "synced": 57, "total": 58, "latest": 76.8},
    "body_fat": {"pending": 0, "synced": 0, "total": 0, "latest": None},
    "heart_rate": {"pending": 1, "synced": 0, "total": 1, "latest": 62},
    "blood_oxygen": {"pending": 0, "synced": 0, "total": 0, "latest": None},
    "blood_sugar": {"pending": 0, "synced": 0, "total": 0, "latest": None},
    "hydration": {"pending": 2, "synced": 0, "total": 2, "latest": 250},
    "rate_limit": {"limit": 250, "remaining": 240},
}
VALUES: dict[str, list[dict[str, Any]]] = {
    "/weight": [
        {"weight_kg": 76.8, "date": "2026-10-10", "time": "07:15:00"},
    ],
    # Raw database columns arrive as strings.
    "/heart-rate": [{"bpm": "62", "date": "2026-10-07", "time": "06:00:00"}],
    "/steps": [
        {"steps": 70, "date": "2026-10-10", "start_time": "11:00:00", "end_time": None},
        {"steps": 4000, "date": "2026-10-10", "start_time": None, "end_time": None},
    ],
    "/hydration": [
        {"milliliters": "250", "date": "2026-10-10", "time": "11:30:00"},
        {"milliliters": "500", "date": "2026-10-10", "time": "08:00:00"},
    ],
}


async def _setup(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    stats: dict[str, Any] | None = None,
) -> None:
    freezer.move_to(NOW)
    mock_values_api(aioclient_mock, STATS if stats is None else stats, VALUES)
    await setup_integration(hass, entry)


def _state(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    entry: MockConfigEntry,
    key: str,
) -> Any:
    entity_id = entity_id_for(entity_registry, entry, "sensor", f"value_{key}")
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state


async def _refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.values.async_refresh()
    await hass.async_block_till_done()


async def test_value_sensors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    await _setup(hass, aioclient_mock, mock_config_entry, freezer)

    weight = _state(hass, entity_registry, mock_config_entry, "weight")
    assert float(weight.state) == 76.8
    assert weight.attributes["unit_of_measurement"] == "kg"
    assert weight.attributes["measured_at"] == "2026-10-10T07:15:00+02:00"
    assert weight.attributes["stale"] is False
    assert weight.name == "Syncvita Tim Weight"

    heart_rate = _state(hass, entity_registry, mock_config_entry, "heart_rate")
    assert float(heart_rate.state) == 62
    assert heart_rate.attributes["stale"] is True

    steps = _state(hass, entity_registry, mock_config_entry, "steps")
    assert float(steps.state) == 4070
    assert steps.attributes["measured_at"] == "2026-10-10T11:00:00+02:00"
    assert "stale" not in steps.attributes

    hydration = _state(hass, entity_registry, mock_config_entry, "hydration")
    assert float(hydration.state) == 750
    assert hydration.attributes["unit_of_measurement"] == "mL"

    for key in ("body_fat", "blood_oxygen", "blood_sugar"):
        assert _state(hass, entity_registry, mock_config_entry, key).state == (
            STATE_UNKNOWN
        )
    assert calls_to(aioclient_mock, "/body-fat") == 0


async def test_query_parameters(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    await _setup(hass, aioclient_mock, mock_config_entry, freezer)
    queries = {
        call[1].path: dict(call[1].query)
        for call in aioclient_mock.mock_calls
        if call[1].path != "/v1/stats"
    }
    assert queries == {
        "/v1/weight": {"status": "all", "limit": "1"},
        "/v1/heart-rate": {"limit": "1"},
        "/v1/steps": {
            "status": "all",
            "date_from": "2026-10-10",
            "date_to": "2026-10-10",
            "limit": "1000",
        },
        "/v1/hydration": {"date": "2026-10-10", "limit": "100"},
    }


async def test_details_only_after_a_change(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    await _setup(hass, aioclient_mock, mock_config_entry, freezer)
    assert calls_to(aioclient_mock, "/weight") == 1

    freezer.tick(timedelta(minutes=15))
    await _refresh(hass, mock_config_entry)
    assert calls_to(aioclient_mock, "/stats") == 2
    assert calls_to(aioclient_mock, "/weight") == 1

    stats = json.loads(json.dumps(STATS))
    stats["weight"]["total"] = 59
    aioclient_mock.clear_requests()
    mock_values_api(
        aioclient_mock,
        stats,
        {**VALUES, "/weight": [{"weight_kg": "75.9", "date": "2026-10-10"}]},
    )
    freezer.tick(timedelta(minutes=15))
    await _refresh(hass, mock_config_entry)
    assert calls_to(aioclient_mock, "/weight") == 1
    assert calls_to(aioclient_mock, "/steps") == 0
    weight = _state(hass, entity_registry, mock_config_entry, "weight")
    assert float(weight.state) == 75.9
    assert weight.attributes["measured_at"] == "2026-10-10T00:00:00+02:00"

    # Once an hour everything is read again.
    freezer.tick(timedelta(hours=1))
    await _refresh(hass, mock_config_entry)
    assert calls_to(aioclient_mock, "/steps") == 1


async def test_daily_totals_start_at_zero(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    await _setup(hass, aioclient_mock, mock_config_entry, freezer)
    freezer.move_to("2026-10-10 22:30:00+00:00")  # 00:30 next day in Berlin
    aioclient_mock.clear_requests()
    mock_values_api(aioclient_mock, STATS, {**VALUES, "/steps": [], "/hydration": []})
    await _refresh(hass, mock_config_entry)
    steps_call = next(c for c in aioclient_mock.mock_calls if c[1].path == "/v1/steps")
    assert steps_call[1].query["date_from"] == "2026-10-11"
    steps = _state(hass, entity_registry, mock_config_entry, "steps")
    assert float(steps.state) == 0
    assert steps.attributes["measured_at"] is None


async def test_day_change_before_refresh_shows_zero(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    await _setup(hass, aioclient_mock, mock_config_entry, freezer)
    freezer.move_to("2026-10-10 22:30:00+00:00")
    sensor = next(
        entity
        for entity in hass.data["sensor"].entities
        if entity.unique_id == f"{mock_config_entry.entry_id}_value_steps"
    )
    assert sensor.native_value == 0


async def test_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    await _setup(hass, aioclient_mock, mock_config_entry, freezer)

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/stats", status=502, text="Bad Gateway")
    await _refresh(hass, mock_config_entry)
    weight = _state(hass, entity_registry, mock_config_entry, "weight")
    assert weight.state == STATE_UNAVAILABLE

    stats = json.loads(json.dumps(STATS))
    stats["weight"]["total"] = 59
    aioclient_mock.clear_requests()
    mock_values_api(aioclient_mock, stats, {"/weight": [{"date": "2026-10-10"}]})
    await _refresh(hass, mock_config_entry)
    assert not mock_config_entry.runtime_data.values.last_update_success

    aioclient_mock.clear_requests()
    aioclient_mock.get(
        f"{API}/stats",
        status=401,
        json={"success": False, "error": {"code": "INVALID_API_KEY"}},
    )
    await _refresh(hass, mock_config_entry)
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == SOURCE_REAUTH for flow in flows)


async def test_rate_limit_pauses_reading(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    await _setup(hass, aioclient_mock, mock_config_entry, freezer)
    rate_limit = mock_config_entry.runtime_data.client.rate_limit
    rate_limit.remaining = 5
    rate_limit.reset_at = mock_config_entry.runtime_data.values.data[
        "weight"
    ].fetched + timedelta(minutes=30)
    await _refresh(hass, mock_config_entry)
    assert calls_to(aioclient_mock, "/stats") == 1


async def test_diagnostics_without_values(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    await _setup(hass, aioclient_mock, mock_config_entry, freezer)
    diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert diagnostics["values"]["types"]["weight"] == {
        "total": 58,
        "measured_at": "2026-10-10T07:15:00+02:00",
    }
    text = json.dumps(diagnostics["values"])
    assert "76.8" not in text
    assert "4070" not in text
