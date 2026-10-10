"""Tests for the entities of the export part."""

from __future__ import annotations

import copy
from datetime import timedelta
import json
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.syncvita.const import (
    CONF_READ_ENABLED,
    CONF_SLEEP_ENABLED,
    CONF_WORKOUTS_ENABLED,
)

from .conftest import (
    API,
    FIXTURES,
    entity_id_for,
    load_fixture,
    make_entry,
    mock_export_api,
    setup_integration,
)

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from pytest_homeassistant_custom_component.test_util.aiohttp import (
        AiohttpClientMocker,
    )

NOW = "2026-10-10 10:00:00+00:00"


async def _setup(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(NOW)
    mock_export_api(aioclient_mock)
    await setup_integration(hass, entry)


async def _refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    coordinator = entry.runtime_data.coordinator
    assert coordinator is not None
    await coordinator.async_refresh()
    await hass.async_block_till_done()


def _state(hass: HomeAssistant, entity_id: str) -> Any:
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state


async def test_status_entities(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    export_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    await _setup(hass, aioclient_mock, export_config_entry, freezer)

    def eid(platform: str, key: str) -> str:
        return entity_id_for(entity_registry, export_config_entry, platform, key)

    active = _state(hass, eid("binary_sensor", "export_active"))
    assert active.state == "on"
    assert "sleep_analysis" in active.attributes["released_types"]
    upload = _state(hass, eid("sensor", "last_upload"))
    assert upload.state == "2026-10-10T06:12:03+00:00"
    assert upload.attributes["age_minutes"] == 227
    stored = _state(hass, eid("sensor", "stored_values"))
    expected = sum(
        m["count"] for m in load_fixture("export_metrics.json")["data"]["metrics"]
    )
    assert stored.state == str(expected)
    assert stored.attributes["counts"]["step_count"] == 9012


async def test_metric_sensors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    export_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    await _setup(hass, aioclient_mock, export_config_entry, freezer)

    def eid(metric: str) -> str:
        return entity_id_for(
            entity_registry, export_config_entry, "sensor", f"metric_{metric}"
        )

    resting = _state(hass, eid("resting_heart_rate"))
    assert resting.state == "52.0"
    assert resting.attributes["unit_of_measurement"] == "bpm"
    assert resting.attributes["state_class"] == "measurement"
    assert resting.attributes["stale"] is False
    assert resting.attributes["measured_at"] == "2026-10-09T07:12:00+02:00"

    steps = _state(hass, eid("step_count"))
    assert steps.state == "8421.0"
    assert steps.attributes["state_class"] == "total_increasing"

    for disabled in ("body_temperature", "new_future_type"):
        entry = entity_registry.async_get(eid(disabled))
        assert entry is not None
        assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    unknown = entity_registry.async_get(eid("new_future_type"))
    assert unknown is not None
    assert unknown.original_name == "New future type"

    for missing in ("walking_speed", "blood_pressure", "sleep_analysis", "workouts"):
        assert (
            entity_registry.async_get_entity_id(
                "sensor", "syncvita", f"{export_config_entry.entry_id}_metric_{missing}"
            )
            is None
        )


async def test_new_metric_appears_after_refresh(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    export_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    await _setup(hass, aioclient_mock, export_config_entry, freezer)
    metrics = copy.deepcopy(load_fixture("export_metrics.json"))
    metrics["data"]["metrics"].append(
        {
            "metric": "vo2_max",
            "kind": "quantity",
            "unit": "mL/(kg*min)",
            "cumulative": False,
            "count": 1,
            "first_date": "2026-10-10",
            "last_date": "2026-10-10",
            "latest": {"value": 44.1, "date": "2026-10-10", "time": "09:00:00"},
        }
    )
    aioclient_mock.clear_requests()
    mock_export_api(aioclient_mock, metrics=metrics)
    await _refresh(hass, export_config_entry)
    vo2 = _state(
        hass,
        entity_id_for(entity_registry, export_config_entry, "sensor", "metric_vo2_max"),
    )
    assert vo2.state == "44.1"
    assert vo2.attributes["unit_of_measurement"] == "mL/(kg*min)"


async def test_today_and_stale(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    export_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    await _setup(hass, aioclient_mock, export_config_entry, freezer)
    freezer.tick(timedelta(days=2))
    aioclient_mock.clear_requests()
    # 2026-10-12 10:00 UTC is the same day in Berlin.
    mock_export_api(aioclient_mock, freezer.time_to_freeze.date())
    await _refresh(hass, export_config_entry)

    def eid(metric: str) -> str:
        return entity_id_for(
            entity_registry, export_config_entry, "sensor", f"metric_{metric}"
        )

    assert _state(hass, eid("step_count")).state == "0"
    resting = _state(hass, eid("resting_heart_rate"))
    assert resting.state == "52.0"
    assert resting.attributes["stale"] is True


async def test_sleep_and_workout_sensors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    export_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    await _setup(hass, aioclient_mock, export_config_entry, freezer)

    def eid(key: str) -> str:
        return entity_id_for(entity_registry, export_config_entry, "sensor", key)

    sleep = _state(hass, eid("sleep_last_night"))
    assert sleep.state == "6.87"
    assert sleep.attributes["unit_of_measurement"] == "h"
    assert sleep.attributes["night"] == "2026-10-10"
    assert sleep.attributes["stages_minutes"]["deep"] == 62.0
    workout = _state(hass, eid("last_workout"))
    assert workout.state == "running"
    assert workout.attributes["duration_minutes"] == 30.5
    assert workout.attributes["distance_m"] == 5012.0
    assert _state(hass, eid("workouts_today")).state == "1"


async def test_export_disabled_makes_value_sensors_unavailable(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    export_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    await _setup(hass, aioclient_mock, export_config_entry, freezer)
    aioclient_mock.clear_requests()
    aioclient_mock.get(
        f"{API}/health-export/metrics",
        status=403,
        json={"success": False, "error": {"code": "EXPORT_DISABLED", "message": "off"}},
    )
    await _refresh(hass, export_config_entry)

    def eid(platform: str, key: str) -> str:
        return entity_id_for(entity_registry, export_config_entry, platform, key)

    assert _state(hass, eid("binary_sensor", "export_active")).state == "off"
    assert (
        _state(hass, eid("sensor", "metric_resting_heart_rate")).state
        == STATE_UNAVAILABLE
    )
    assert _state(hass, eid("sensor", "sleep_last_night")).state == STATE_UNAVAILABLE


async def test_event_entity_receives_workout(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    export_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    await _setup(hass, aioclient_mock, export_config_entry, freezer)
    event_id = entity_id_for(entity_registry, export_config_entry, "event", "workout")
    assert _state(hass, event_id).state == "unknown"
    hass.bus.async_fire(
        "syncvita_workout_finished",
        {"config_entry_id": "someone_else", "activity_type": "yoga"},
    )
    await hass.async_block_till_done()
    assert _state(hass, event_id).state == "unknown"
    hass.bus.async_fire(
        "syncvita_workout_finished",
        {"config_entry_id": export_config_entry.entry_id, "activity_type": "cycling"},
    )
    await hass.async_block_till_done()
    state = _state(hass, event_id)
    assert state.attributes["event_type"] == "workout_finished"
    assert state.attributes["activity_type"] == "cycling"
    assert "config_entry_id" not in state.attributes


async def test_disabled_options_skip_entities(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    entry = make_entry(**{CONF_READ_ENABLED: True, CONF_SLEEP_ENABLED: False})
    await _setup(hass, aioclient_mock, entry, freezer)
    for platform, key in (("sensor", "sleep_last_night"), ("event", "sleep")):
        assert (
            entity_registry.async_get_entity_id(
                platform, "syncvita", f"{entry.entry_id}_{key}"
            )
            is None
        )


async def _reload_with(
    hass: HomeAssistant, entry: MockConfigEntry, **options: Any
) -> None:
    hass.config_entries.async_update_entry(entry, options={**entry.options, **options})
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


def _keys(entity_registry: er.EntityRegistry, entry: MockConfigEntry) -> set[str]:
    prefix = f"{entry.entry_id}_"
    return {
        entity.unique_id.removeprefix(prefix)
        for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    }


async def test_switched_off_features_remove_their_entities(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    export_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    await _setup(hass, aioclient_mock, export_config_entry, freezer)
    sleep = {"sleep_last_night", "sleep"}
    workout = {"last_workout", "workouts_today", "workout"}
    keys = _keys(entity_registry, export_config_entry)
    assert sleep | workout | {"export_active", "metric_resting_heart_rate"} <= keys

    await _reload_with(hass, export_config_entry, **{CONF_SLEEP_ENABLED: False})
    keys = _keys(entity_registry, export_config_entry)
    assert not keys & sleep
    assert workout <= keys

    await _reload_with(hass, export_config_entry, **{CONF_WORKOUTS_ENABLED: False})
    keys = _keys(entity_registry, export_config_entry)
    assert not keys & (sleep | workout)
    assert "export_active" in keys

    await _reload_with(hass, export_config_entry, **{CONF_READ_ENABLED: False})
    assert _keys(entity_registry, export_config_entry) == {
        "last_transfer",
        "sent_values",
        "queue",
        "last_error",
        "rate_limit_remaining",
        "process_queue",
    }


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_real_metrics_produce_valid_sensors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    export_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    real = json.loads(
        (FIXTURES / "real" / "health_export_metrics.json").read_text(encoding="utf-8")
    )
    freezer.move_to(NOW)
    mock_export_api(aioclient_mock, metrics=real["body"])
    await setup_integration(hass, export_config_entry)
    assert "state class" not in caplog.text
    assert "device class" not in caplog.text
