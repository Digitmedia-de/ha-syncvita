"""Tests for the sending part."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from homeassistant.components.persistent_notification import (
    _async_get_or_create_notifications,
)
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.syncvita.const import CONF_NOTIFY_ERRORS, DOMAIN
from custom_components.syncvita.measures import Measure, build_measurement
from custom_components.syncvita.sender import queued_entry

from .conftest import API, WATER_ATTRS, WEIGHT_ATTRS, make_entry, setup_integration

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.test_util.aiohttp import (
        AiohttpClientMocker,
    )

pytestmark = pytest.mark.usefixtures("source_states")

NOTIFY = "custom_components.syncvita.sender.persistent_notification.async_create"
DISMISS = "custom_components.syncvita.sender.persistent_notification.async_dismiss"
REAUTH = "homeassistant.config_entries.ConfigEntry.async_start_reauth"
OK = {"success": True}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"success": False, "error": {"code": code, "message": message}}


async def _set(
    hass: HomeAssistant, entity_id: str, value: str, attrs: dict[str, Any]
) -> None:
    hass.states.async_set(entity_id, value, attrs)
    await hass.async_block_till_done()


async def _tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: int
) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _stored_queue(entry: MockConfigEntry, *entries: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.{entry.entry_id}",
        "data": {"queue": list(entries)},
    }


def _queued(key: str, measured_at: str, weight: float) -> dict[str, Any]:
    local = dt_util.as_local(dt_util.parse_datetime(measured_at))
    return {
        "source": "sensor.scale_weight",
        "key": key,
        "measure": "weight",
        "measured_at": measured_at,
        "date": local.strftime("%Y-%m-%d"),
        "time": local.strftime("%H:%M:%S"),
        "values": {"weight": weight},
    }


async def test_weight_sent_with_local_date_and_time(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    aioclient_mock.post(f"{API}/weight", status=201, json=OK)
    await setup_integration(hass, mock_config_entry)
    freezer.move_to("2026-10-10 05:30:00+00:00")
    await _set(hass, "sensor.scale_weight", "81.234", WEIGHT_ATTRS)
    assert aioclient_mock.call_count == 1
    assert aioclient_mock.mock_calls[0][2] == {
        "weight": 81.23,
        "date": "2026-10-10",
        "time": "07:30:00",
    }
    assert mock_config_entry.runtime_data.store.sent_count == 1


async def test_first_state_skipped_then_litres_converted(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    aioclient_mock.post(f"{API}/hydration", status=201, json=OK)
    await setup_integration(hass, mock_config_entry)
    await _set(hass, "sensor.water", "0.5", WATER_ATTRS)
    assert aioclient_mock.call_count == 0
    await _set(hass, "sensor.water", "0.75", WATER_ATTRS)
    assert aioclient_mock.mock_calls[0][2]["milliliters"] == 750


async def test_unit_switch_to_pounds(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    aioclient_mock.post(f"{API}/weight", status=201, json=OK)
    await setup_integration(hass, mock_config_entry)
    await _set(hass, "sensor.scale_weight", "180.5", {"unit_of_measurement": "lb"})
    body = aioclient_mock.mock_calls[0][2]
    assert body["weight"] == 180.5
    assert body["unit"] == "lbs"


async def test_unsupported_unit_is_skipped(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    aioclient_mock.post(f"{API}/weight", status=201, json=OK)
    await setup_integration(hass, mock_config_entry)
    await _set(hass, "sensor.scale_weight", "12.6", {"unit_of_measurement": "st"})
    assert aioclient_mock.call_count == 0
    assert "unsupported unit" in caplog.text


@pytest.mark.parametrize(
    "sequence",
    [
        ["unavailable", "81.0"],
        ["unknown", "81.0"],
        ["80.0", "abc"],
        ["unavailable", "80.0"],
    ],
)
async def test_guards_skip(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    sequence: list[str],
) -> None:
    aioclient_mock.post(f"{API}/weight", status=201, json=OK)
    await setup_integration(hass, mock_config_entry)
    for value in sequence:
        await _set(hass, "sensor.scale_weight", value, WEIGHT_ATTRS)
    assert aioclient_mock.call_count == 0


async def test_attribute_only_change_not_sent(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    aioclient_mock.post(f"{API}/weight", status=201, json=OK)
    await setup_integration(hass, mock_config_entry)
    await _set(
        hass, "sensor.scale_weight", "80.0", {**WEIGHT_ATTRS, "friendly_name": "Waage"}
    )
    assert aioclient_mock.call_count == 0


async def test_no_duplicate_after_restart(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    aioclient_mock.post(f"{API}/weight", status=201, json=OK)
    await setup_integration(hass, mock_config_entry)
    await _set(hass, "sensor.scale_weight", "81.0", WEIGHT_ATTRS)
    assert aioclient_mock.call_count == 1

    assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get("sensor.scale_weight")
    assert state is not None
    measurement = build_measurement(Measure.WEIGHT, 81.0, "kg", state.last_changed)
    sender = mock_config_entry.runtime_data.sender
    assert await sender.async_submit("sensor.scale_weight", measurement) is False
    assert aioclient_mock.call_count == 1


async def test_400_not_retried_and_notified(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    aioclient_mock.post(
        f"{API}/weight", status=400, json=_error("VALIDATION_ERROR", "weight too high")
    )
    await setup_integration(hass, mock_config_entry)
    with patch(NOTIFY) as notify:
        await _set(hass, "sensor.scale_weight", "81.0", WEIGHT_ATTRS)
    runtime = mock_config_entry.runtime_data
    assert runtime.store.queue == []
    assert runtime.sender.last_error == "weight too high"
    assert runtime.sender.last_error_status == 400
    notify.assert_called_once()
    assert "weight too high" in notify.call_args.args[1]
    await _tick(hass, freezer, 3600)
    assert aioclient_mock.call_count == 1


async def test_notifications_can_be_disabled(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    entry = make_entry(**{CONF_NOTIFY_ERRORS: False})
    aioclient_mock.post(
        f"{API}/weight", status=400, json=_error("VALIDATION_ERROR", "bad")
    )
    await setup_integration(hass, entry)
    with patch(NOTIFY) as notify:
        await _set(hass, "sensor.scale_weight", "81.0", WEIGHT_ATTRS)
    notify.assert_not_called()


async def test_401_queues_and_starts_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    aioclient_mock.post(
        f"{API}/weight", status=401, json=_error("UNAUTHORIZED", "Invalid API key")
    )
    await setup_integration(hass, mock_config_entry)
    with patch(REAUTH) as reauth:
        await _set(hass, "sensor.scale_weight", "81.0", WEIGHT_ATTRS)
    reauth.assert_called_once()
    assert len(mock_config_entry.runtime_data.store.queue) == 1


@pytest.mark.parametrize(
    "response",
    [
        {"status": 500, "json": _error("DATABASE_ERROR", "db down")},
        {"status": 502, "text": "<html>Bad Gateway</html>"},
    ],
)
async def test_server_error_queues_and_bulk_retries(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    response: dict[str, Any],
) -> None:
    aioclient_mock.post(f"{API}/weight", **response)
    await setup_integration(hass, mock_config_entry)
    await _set(hass, "sensor.scale_weight", "81.0", WEIGHT_ATTRS)
    runtime = mock_config_entry.runtime_data
    assert len(runtime.store.queue) == 1
    assert runtime.sender.last_error_status == response["status"]

    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{API}/bulk", status=201, json=OK)
    with patch(DISMISS) as dismiss:
        await _tick(hass, freezer, 61)
    assert aioclient_mock.call_count == 1
    body = aioclient_mock.mock_calls[0][2]
    assert body["options"] == {"continue_on_error": True}
    assert body["data"][0]["type"] == "weight"
    assert body["data"][0]["values"] == {"weight": 81.0}
    assert runtime.store.queue == []
    assert runtime.store.sent_count == 1
    assert runtime.sender.last_error is None
    dismiss.assert_called()


async def test_backoff_increases(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    aioclient_mock.post(
        f"{API}/weight", status=500, json=_error("DATABASE_ERROR", "db")
    )
    aioclient_mock.post(f"{API}/bulk", status=500, json=_error("DATABASE_ERROR", "db"))
    await setup_integration(hass, mock_config_entry)
    await _set(hass, "sensor.scale_weight", "81.0", WEIGHT_ATTRS)
    assert aioclient_mock.call_count == 1
    await _tick(hass, freezer, 61)
    assert aioclient_mock.call_count == 2
    await _tick(hass, freezer, 61)
    assert aioclient_mock.call_count == 2
    await _tick(hass, freezer, 60)
    assert aioclient_mock.call_count == 3


async def test_429_uses_retry_after(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    aioclient_mock.post(
        f"{API}/weight",
        status=429,
        json=_error("RATE_LIMIT_EXCEEDED", "slow down"),
        headers={"Retry-After": "120"},
    )
    aioclient_mock.post(f"{API}/bulk", status=201, json=OK)
    await setup_integration(hass, mock_config_entry)
    await _set(hass, "sensor.scale_weight", "81.0", WEIGHT_ATTRS)
    await _tick(hass, freezer, 61)
    assert aioclient_mock.call_count == 1
    await _tick(hass, freezer, 60)
    assert aioclient_mock.call_count == 2


async def test_rate_limit_pauses_sending(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to("2026-10-10 05:30:00+00:00")
    reset = dt_util.utcnow() + timedelta(minutes=10)
    aioclient_mock.post(
        f"{API}/weight",
        status=201,
        json=OK,
        headers={
            "X-RateLimit-Remaining": "5",
            "X-RateLimit-Reset": str(int(reset.timestamp())),
        },
    )
    aioclient_mock.post(f"{API}/bulk", status=201, json=OK)
    await setup_integration(hass, mock_config_entry)
    await _set(hass, "sensor.scale_weight", "81.0", WEIGHT_ATTRS)
    await _set(hass, "sensor.scale_weight", "82.0", WEIGHT_ATTRS)
    assert aioclient_mock.call_count == 1
    assert len(mock_config_entry.runtime_data.store.queue) == 1
    await _tick(hass, freezer, 601)
    assert aioclient_mock.call_count == 2
    assert aioclient_mock.mock_calls[1][1].path == "/v1/bulk"


async def test_live_success_flushes_queue(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    aioclient_mock.post(
        f"{API}/weight", status=500, json=_error("DATABASE_ERROR", "db")
    )
    await setup_integration(hass, mock_config_entry)
    await _set(hass, "sensor.scale_weight", "81.0", WEIGHT_ATTRS)
    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{API}/weight", status=201, json=OK)
    aioclient_mock.post(f"{API}/bulk", status=201, json=OK)
    await _set(hass, "sensor.scale_weight", "82.0", WEIGHT_ATTRS)
    assert [call[1].path for call in aioclient_mock.mock_calls] == [
        "/v1/weight",
        "/v1/bulk",
    ]
    assert mock_config_entry.runtime_data.store.queue == []


async def test_expired_queue_entries_dropped(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to("2026-10-10 10:00:00+00:00")
    hass_storage[f"{DOMAIN}.{mock_config_entry.entry_id}"] = _stored_queue(
        mock_config_entry,
        _queued("old", "2026-10-06T10:00:00+00:00", 80.0),
        _queued("fresh", "2026-10-10T08:00:00+00:00", 80.5),
    )
    aioclient_mock.post(f"{API}/bulk", status=201, json=OK)
    await setup_integration(hass, mock_config_entry)
    assert aioclient_mock.call_count == 1
    sent = aioclient_mock.mock_calls[0][2]["data"]
    assert [entry["values"] for entry in sent] == [{"weight": 80.5}]
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"expired_entries_{mock_config_entry.entry_id}"
    )
    assert issue is not None
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders["count"] == "1"


async def test_bulk_partial_failure(  # noqa: PLR0917
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    freezer.move_to("2026-10-10 10:00:00+00:00")
    hass_storage[f"{DOMAIN}.{mock_config_entry.entry_id}"] = _stored_queue(
        mock_config_entry,
        _queued("a", "2026-10-10T08:00:00+00:00", 80.5),
        _queued("b", "2026-10-10T08:05:00+00:00", 600.0),
    )
    aioclient_mock.post(
        f"{API}/bulk",
        status=201,
        json={
            "success": True,
            "data": {
                "results": [
                    {"index": 0, "type": "weight", "status": "created", "id": 1},
                    {
                        "index": 1,
                        "type": "weight",
                        "status": "failed",
                        "error": "out of range",
                    },
                ]
            },
        },
    )
    await setup_integration(hass, mock_config_entry)
    store = mock_config_entry.runtime_data.store
    assert store.queue == []
    assert store.sent_count == 1
    assert "out of range" in caplog.text
    notifications = _async_get_or_create_notifications(hass)
    assert f"{DOMAIN}_{mock_config_entry.entry_id}" in notifications
    assert mock_config_entry.runtime_data.sender.last_error is not None


async def test_bulk_validation_error_drops_chunk(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to("2026-10-10 10:00:00+00:00")
    hass_storage[f"{DOMAIN}.{mock_config_entry.entry_id}"] = _stored_queue(
        mock_config_entry, _queued("a", "2026-10-10T08:00:00+00:00", 80.5)
    )
    aioclient_mock.post(
        f"{API}/bulk", status=400, json=_error("BULK_VALIDATION_ERROR", "invalid")
    )
    await setup_integration(hass, mock_config_entry)
    runtime = mock_config_entry.runtime_data
    assert runtime.store.queue == []
    assert runtime.store.sent_count == 0
    assert runtime.sender.last_error_status == 400


async def test_bulk_auth_error_keeps_queue(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to("2026-10-10 10:00:00+00:00")
    hass_storage[f"{DOMAIN}.{mock_config_entry.entry_id}"] = _stored_queue(
        mock_config_entry, _queued("a", "2026-10-10T08:00:00+00:00", 80.5)
    )
    aioclient_mock.post(f"{API}/bulk", status=401, json=_error("UNAUTHORIZED", "nope"))
    with patch(REAUTH) as reauth:
        await setup_integration(hass, mock_config_entry)
    reauth.assert_called_once()
    assert len(mock_config_entry.runtime_data.store.queue) == 1


async def test_queue_is_sent_in_chunks_of_100(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to("2026-10-10 10:00:00+00:00")
    entries = [
        _queued(f"k{index}", "2026-10-10T08:00:00+00:00", 80 + index / 100)
        for index in range(150)
    ]
    hass_storage[f"{DOMAIN}.{mock_config_entry.entry_id}"] = _stored_queue(
        mock_config_entry, *entries
    )
    aioclient_mock.post(f"{API}/bulk", status=201, json=OK)
    await setup_integration(hass, mock_config_entry)
    assert [len(call[2]["data"]) for call in aioclient_mock.mock_calls] == [100, 50]
    assert mock_config_entry.runtime_data.store.sent_count == 150


async def test_queue_pauses_when_rate_limit_drops(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to("2026-10-10 10:00:00+00:00")
    entries = [
        _queued(f"k{index}", "2026-10-10T08:00:00+00:00", 80 + index / 100)
        for index in range(150)
    ]
    hass_storage[f"{DOMAIN}.{mock_config_entry.entry_id}"] = _stored_queue(
        mock_config_entry, *entries
    )
    reset = dt_util.utcnow() + timedelta(minutes=10)
    aioclient_mock.post(
        f"{API}/bulk",
        status=201,
        json=OK,
        headers={
            "X-RateLimit-Remaining": "2",
            "X-RateLimit-Reset": str(int(reset.timestamp())),
        },
    )
    await setup_integration(hass, mock_config_entry)
    assert aioclient_mock.call_count == 1
    assert len(mock_config_entry.runtime_data.store.queue) == 50

    await _tick(hass, freezer, 601)
    assert aioclient_mock.call_count == 2
    assert mock_config_entry.runtime_data.store.queue == []


async def test_send_bulk_directly(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to("2026-10-10 10:00:00+00:00")
    aioclient_mock.post(f"{API}/bulk", status=201, json=OK)
    await setup_integration(hass, mock_config_entry)
    measurement = build_measurement(
        Measure.WEIGHT, 79.0, "kg", dt_util.utcnow() - timedelta(days=30)
    )
    sender = mock_config_entry.runtime_data.sender
    result = await sender.async_send_bulk(
        [queued_entry("sensor.scale_weight", measurement)]
    )
    assert result == (1, 0)
    assert mock_config_entry.runtime_data.store.is_known(
        "sensor.scale_weight", measurement.key
    )


async def test_send_bulk_partial_failure_keeps_notification(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to("2026-10-10 10:00:00+00:00")
    aioclient_mock.post(
        f"{API}/bulk",
        status=201,
        json={
            "success": True,
            "data": {
                "results": [
                    {"index": 0, "type": "weight", "status": "created", "id": 1},
                    {
                        "index": 1,
                        "type": "weight",
                        "status": "failed",
                        "error": "out of range",
                    },
                ]
            },
        },
    )
    await setup_integration(hass, mock_config_entry)
    sender = mock_config_entry.runtime_data.sender
    moment = dt_util.utcnow() - timedelta(days=30)
    result = await sender.async_send_bulk(
        [
            queued_entry(
                "sensor.scale_weight",
                build_measurement(Measure.WEIGHT, 79.0, "kg", moment),
            ),
            queued_entry(
                "sensor.scale_weight",
                build_measurement(
                    Measure.WEIGHT, 81.0, "kg", moment + timedelta(minutes=5)
                ),
            ),
        ]
    )
    assert result == (1, 1)
    notifications = _async_get_or_create_notifications(hass)
    assert f"{DOMAIN}_{mock_config_entry.entry_id}" in notifications
    assert sender.last_error is not None
