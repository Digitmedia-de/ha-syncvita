"""Tests for the export coordinator."""

from __future__ import annotations

import copy
from datetime import UTC, date, datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.syncvita.const import (
    CONF_READ_ENABLED,
    CONF_SLEEP_ENABLED,
    CONF_WORKOUTS_ENABLED,
    DOMAIN,
    EVENT_SLEEP_RECORDED,
    EVENT_WORKOUT_FINISHED,
)

from .conftest import (
    API,
    calls_to,
    entity_id_for,
    load_fixture,
    make_entry,
    mock_export_api,
    setup_integration,
)

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.test_util.aiohttp import (
        AiohttpClientMocker,
    )

NOW = "2026-10-10 10:00:00+00:00"
W1 = "11111111-1111-4111-8111-111111111111"
W2 = "22222222-2222-4222-8222-222222222222"
W3 = "33333333-3333-4333-8333-333333333333"


def _metrics_with(name: str, **changes: Any) -> dict[str, Any]:
    metrics = copy.deepcopy(load_fixture("export_metrics.json"))
    for metric in metrics["data"]["metrics"]:
        if metric["metric"] == name:
            metric.update(changes)
    return metrics


def _workouts_with_new() -> dict[str, Any]:
    workouts = copy.deepcopy(load_fixture("export_daily_workouts.json"))
    new = copy.deepcopy(workouts["data"]["workouts"][0])
    new.update(
        uuid=W3,
        activity_type="cycling",
        start="2026-10-10T11:00:00+02:00",
        end="2026-10-10T12:00:00+02:00",
        duration_seconds=3600,
    )
    workouts["data"]["workouts"].insert(0, new)
    return workouts


async def _refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    coordinator = entry.runtime_data.coordinator
    assert coordinator is not None
    await coordinator.async_refresh()
    await hass.async_block_till_done()


async def test_first_refresh_loads_everything_without_events(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    export_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(NOW)
    mock_export_api(aioclient_mock)
    workout_events = async_capture_events(hass, EVENT_WORKOUT_FINISHED)
    sleep_events = async_capture_events(hass, EVENT_SLEEP_RECORDED)
    await setup_integration(hass, export_config_entry)

    runtime = export_config_entry.runtime_data
    assert runtime.coordinator is not None
    data = runtime.coordinator.data
    assert data.enabled
    assert data.released_types == [
        "step_count",
        "resting_heart_rate",
        "sleep_analysis",
        "workouts",
    ]
    assert data.last_upload == datetime(2026, 10, 10, 6, 12, 3, tzinfo=UTC)
    assert data.sleep is not None
    assert data.sleep.night == "2026-10-10"
    assert data.sleep.duration_hours == 6.87
    assert [workout.uuid for workout in data.workouts] == [W1, W2]
    assert calls_to(aioclient_mock, "/health-export/metrics") == 1
    assert calls_to(aioclient_mock, "/health-export/settings") == 1
    assert calls_to(aioclient_mock, "/health-export/daily", "sleep_analysis") == 1
    assert calls_to(aioclient_mock, "/health-export/daily", "workouts") == 1
    assert workout_events == []
    assert sleep_events == []
    assert runtime.store.export_initialized
    assert runtime.store.seen_workouts == [W2, W1]
    assert runtime.store.last_sleep_night == "2026-10-10"


async def test_unchanged_metrics_poll_only_metrics(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    export_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(NOW)
    mock_export_api(aioclient_mock)
    await setup_integration(hass, export_config_entry)
    freezer.tick(timedelta(minutes=15))
    await _refresh(hass, export_config_entry)
    assert calls_to(aioclient_mock, "/health-export/metrics") == 2
    assert calls_to(aioclient_mock, "/health-export/settings") == 1
    assert calls_to(aioclient_mock, "/health-export/daily") == 2


async def test_hourly_full_refresh(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    export_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(NOW)
    mock_export_api(aioclient_mock)
    await setup_integration(hass, export_config_entry)
    freezer.tick(timedelta(hours=1))
    await _refresh(hass, export_config_entry)
    assert calls_to(aioclient_mock, "/health-export/settings") == 2
    assert calls_to(aioclient_mock, "/health-export/daily") == 4


async def test_new_workout_fires_event_once(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    export_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(NOW)
    mock_export_api(aioclient_mock)
    events = async_capture_events(hass, EVENT_WORKOUT_FINISHED)
    await setup_integration(hass, export_config_entry)

    aioclient_mock.clear_requests()
    mock_export_api(
        aioclient_mock,
        metrics=_metrics_with("workouts", count=3),
        workouts=_workouts_with_new(),
    )
    await _refresh(hass, export_config_entry)
    assert len(events) == 1
    assert events[0].data["config_entry_id"] == export_config_entry.entry_id
    assert events[0].data["activity_type"] == "cycling"
    assert events[0].data["duration_minutes"] == 60.0
    assert events[0].data["energy_kcal"] == 312.5

    await _refresh(hass, export_config_entry)
    assert await hass.config_entries.async_reload(export_config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(events) == 1


async def _setup_then_add_workout(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Set up once, then make the export contain the new workout W3."""
    freezer.move_to(NOW)
    mock_export_api(aioclient_mock)
    await setup_integration(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    aioclient_mock.clear_requests()
    mock_export_api(
        aioclient_mock,
        metrics=_metrics_with("workouts", count=3),
        workouts=_workouts_with_new(),
    )


async def test_workout_found_on_setup_reaches_entity(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    export_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A workout uploaded while the entry was not loaded is announced once."""
    await _setup_then_add_workout(hass, aioclient_mock, export_config_entry, freezer)
    events = async_capture_events(hass, EVENT_WORKOUT_FINISHED)
    assert await hass.config_entries.async_setup(export_config_entry.entry_id)
    await hass.async_block_till_done()

    assert [event.data["uuid"] for event in events] == [W3]
    event_id = entity_id_for(entity_registry, export_config_entry, "event", "workout")
    state = hass.states.get(event_id)
    assert state is not None
    assert state.attributes["event_type"] == "workout_finished"
    assert state.attributes["uuid"] == W3
    assert W3 in export_config_entry.runtime_data.store.seen_workouts

    await _refresh(hass, export_config_entry)
    assert len(events) == 1


async def test_workout_found_during_startup_waits_for_started(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    export_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Events wait for EVENT_HOMEASSISTANT_STARTED and are only then marked seen."""
    await _setup_then_add_workout(hass, aioclient_mock, export_config_entry, freezer)
    events = async_capture_events(hass, EVENT_WORKOUT_FINISHED)
    hass.set_state(CoreState.starting)
    assert await hass.config_entries.async_setup(export_config_entry.entry_id)
    await hass.async_block_till_done()

    store = export_config_entry.runtime_data.store
    assert events == []
    assert W3 not in store.seen_workouts
    # A refresh before the start must not announce or queue the workout twice.
    freezer.tick(timedelta(hours=1))
    await _refresh(hass, export_config_entry)
    assert events == []

    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    assert [event.data["uuid"] for event in events] == [W3]
    assert W3 in store.seen_workouts
    event_id = entity_id_for(entity_registry, export_config_entry, "event", "workout")
    state = hass.states.get(event_id)
    assert state is not None
    assert state.attributes["uuid"] == W3


async def test_new_night_fires_event(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    export_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(NOW)
    mock_export_api(aioclient_mock)
    events = async_capture_events(hass, EVENT_SLEEP_RECORDED)
    await setup_integration(hass, export_config_entry)

    freezer.move_to("2026-10-11 10:00:00+00:00")
    sleep = copy.deepcopy(load_fixture("export_daily_sleep.json"))
    sleep["data"]["nights"].append(
        {
            "date": "2026-10-11",
            "bedtime": "2026-10-10T22:45:00",
            "wake_time": "2026-10-11T06:15:00",
            "asleep_minutes": 420,
            "in_bed_minutes": None,
            "stages": {"core": 260, "deep": 70, "rem": 80, "awake": 10},
        }
    )
    aioclient_mock.clear_requests()
    mock_export_api(
        aioclient_mock,
        date(2026, 10, 11),
        metrics=_metrics_with("sleep_analysis", count=121, last_date="2026-10-11"),
        sleep=sleep,
    )
    await _refresh(hass, export_config_entry)
    assert len(events) == 1
    assert events[0].data["night"] == "2026-10-11"
    assert events[0].data["duration_hours"] == 7.0


async def test_disabled_sleep_and_workouts_skip_daily(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(NOW)
    entry = make_entry(
        **{
            CONF_READ_ENABLED: True,
            CONF_SLEEP_ENABLED: False,
            CONF_WORKOUTS_ENABLED: False,
        }
    )
    mock_export_api(aioclient_mock)
    await setup_integration(hass, entry)
    assert calls_to(aioclient_mock, "/health-export/daily") == 0


@pytest.mark.parametrize(
    "metrics_mock",
    [
        {
            "status": 403,
            "json": {
                "success": False,
                "error": {"code": "EXPORT_DISABLED", "message": "off"},
            },
        },
        {"json": {"success": True, "data": {"enabled": False, "metrics": []}}},
    ],
)
async def test_export_disabled_is_a_state(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    export_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
    metrics_mock: dict[str, Any],
) -> None:
    aioclient_mock.get(f"{API}/health-export/metrics", **metrics_mock)
    with caplog.at_level(logging.WARNING):
        await setup_integration(hass, export_config_entry)
    coordinator = export_config_entry.runtime_data.coordinator
    assert coordinator is not None
    assert coordinator.last_update_success
    assert coordinator.data.enabled is False
    assert not [
        r for r in caplog.records if r.name.startswith("custom_components.syncvita")
    ]


async def test_auth_error_starts_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    export_config_entry: MockConfigEntry,
) -> None:
    aioclient_mock.get(
        f"{API}/health-export/metrics",
        status=401,
        json={
            "success": False,
            "error": {"code": "UNAUTHORIZED", "message": "Invalid API key"},
        },
    )
    await setup_integration(hass, export_config_entry)
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


@pytest.mark.parametrize(
    "metrics_mock",
    [
        {"exc": aiohttp.ClientError()},
        {
            "json": {
                "success": True,
                "data": {"enabled": True, "metrics": [{"count": 3}]},
            }
        },
    ],
)
async def test_update_failed(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    export_config_entry: MockConfigEntry,
    metrics_mock: dict[str, Any],
) -> None:
    aioclient_mock.get(f"{API}/health-export/metrics", **metrics_mock)
    await setup_integration(hass, export_config_entry)
    coordinator = export_config_entry.runtime_data.coordinator
    assert coordinator is not None
    assert coordinator.last_update_success is False


async def test_low_rate_limit_pauses_reading(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    export_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(NOW)
    reset = dt_util.utcnow() + timedelta(minutes=30)
    mock_export_api(
        aioclient_mock,
        headers={
            "X-RateLimit-Remaining": "20",
            "X-RateLimit-Reset": str(int(reset.timestamp())),
        },
    )
    await setup_integration(hass, export_config_entry)
    before = aioclient_mock.call_count
    freezer.tick(timedelta(minutes=15))
    await _refresh(hass, export_config_entry)
    assert aioclient_mock.call_count == before


async def test_requests_per_hour_stay_below_30(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    export_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(NOW)
    mock_export_api(aioclient_mock)
    await setup_integration(hass, export_config_entry)
    coordinator = export_config_entry.runtime_data.coordinator
    assert coordinator is not None
    # Polling only runs while something listens; the entities follow in task 11.
    unsub = coordinator.async_add_listener(lambda: None)
    for _ in range(4):
        freezer.tick(timedelta(minutes=15))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert calls_to(aioclient_mock, "/health-export/metrics") == 5
    assert aioclient_mock.call_count < 30
    unsub()


async def test_stream_switched_on_later_starts_silently(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Enabling workouts/sleep later must not announce the existing items."""
    freezer.move_to(NOW)
    entry = make_entry(
        **{
            CONF_READ_ENABLED: True,
            CONF_SLEEP_ENABLED: False,
            CONF_WORKOUTS_ENABLED: False,
        }
    )
    mock_export_api(aioclient_mock)
    workout_events = async_capture_events(hass, EVENT_WORKOUT_FINISHED)
    sleep_events = async_capture_events(hass, EVENT_SLEEP_RECORDED)
    await setup_integration(hass, entry)
    store = entry.runtime_data.store
    assert not store.is_stream_initialized("workouts")
    assert not store.is_stream_initialized("sleep_analysis")
    assert not store.export_initialized

    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_SLEEP_ENABLED: True,
            CONF_WORKOUTS_ENABLED: True,
        },
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    store = entry.runtime_data.store
    assert workout_events == []
    assert sleep_events == []
    assert store.export_initialized
    assert store.seen_workouts == [W2, W1]
    assert store.last_sleep_night == "2026-10-10"

    aioclient_mock.clear_requests()
    mock_export_api(
        aioclient_mock,
        metrics=_metrics_with("workouts", count=3),
        workouts=_workouts_with_new(),
    )
    await _refresh(hass, entry)
    assert [event.data["uuid"] for event in workout_events] == [W3]


async def test_export_enabled_again_loads_daily_data_at_once(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    export_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(NOW)
    mock_export_api(aioclient_mock)
    await setup_integration(hass, export_config_entry)

    aioclient_mock.clear_requests()
    aioclient_mock.get(
        f"{API}/health-export/metrics",
        status=403,
        json={"success": False, "error": {"code": "EXPORT_DISABLED", "message": "off"}},
    )
    await _refresh(hass, export_config_entry)
    coordinator = export_config_entry.runtime_data.coordinator
    assert coordinator is not None
    assert coordinator.data.enabled is False

    # Same metrics as before, a few minutes later: still read the daily data.
    freezer.tick(timedelta(minutes=15))
    aioclient_mock.clear_requests()
    mock_export_api(aioclient_mock)
    await _refresh(hass, export_config_entry)
    assert coordinator.data.enabled
    assert calls_to(aioclient_mock, "/health-export/daily", "sleep_analysis") == 1
    assert calls_to(aioclient_mock, "/health-export/daily", "workouts") == 1
    assert coordinator.data.sleep is not None
    assert [workout.uuid for workout in coordinator.data.workouts] == [W1, W2]
