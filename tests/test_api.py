"""Tests for the Syncvita API client."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest

from custom_components.syncvita.api import (
    BulkResult,
    SyncvitaAuthError,
    SyncvitaClient,
    SyncvitaConnectionError,
    SyncvitaError,
    SyncvitaExportDisabledError,
    SyncvitaRateLimitError,
    SyncvitaValidationError,
)

from .conftest import API, API_KEY

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.test_util.aiohttp import (
        AiohttpClientMocker,
    )


def _error(code: str, message: str = "error") -> dict[str, Any]:
    return {"success": False, "error": {"code": code, "message": message}}


@pytest.fixture
async def client(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> SyncvitaClient:
    return SyncvitaClient(async_get_clientsession(hass), API_KEY)


async def test_get_stats_sends_bearer_token(
    client: SyncvitaClient, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{API}/stats", json={"success": True, "data": {}})
    assert await client.async_get_stats() == {"success": True, "data": {}}
    headers = aioclient_mock.mock_calls[0][3]
    assert headers["Authorization"] == f"Bearer {API_KEY}"
    assert "X-API-Version" not in headers


async def test_export_daily_sends_version_and_params(
    client: SyncvitaClient, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(
        f"{API}/health-export/daily",
        params={"metric": "step_count", "from": "2026-10-09", "to": "2026-10-10"},
        json={"success": True, "data": {"metric": "step_count", "days": []}},
    )
    data = await client.async_get_export_daily(
        "step_count", date(2026, 10, 9), date(2026, 10, 10)
    )
    assert data == {"metric": "step_count", "days": []}
    assert aioclient_mock.mock_calls[0][3]["X-API-Version"] == "1.6"


async def test_export_metrics_and_settings_return_data(
    client: SyncvitaClient, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(
        f"{API}/health-export/metrics",
        json={"success": True, "data": {"enabled": True}},
    )
    aioclient_mock.get(
        f"{API}/health-export/settings", json={"success": True, "data": {"types": []}}
    )
    assert await client.async_get_export_metrics() == {"enabled": True}
    assert await client.async_get_export_settings() == {"types": []}


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (400, _error("VALIDATION_ERROR", "weight too high"), SyncvitaValidationError),
        (401, _error("UNAUTHORIZED", "Invalid API key"), SyncvitaAuthError),
        (403, _error("EXPORT_DISABLED"), SyncvitaExportDisabledError),
        (403, _error("FORBIDDEN"), SyncvitaError),
        (404, _error("NOT_FOUND"), SyncvitaError),
        (500, _error("DATABASE_ERROR"), SyncvitaConnectionError),
        (502, None, SyncvitaConnectionError),
    ],
)
async def test_status_mapping(
    client: SyncvitaClient,
    aioclient_mock: AiohttpClientMocker,
    status: int,
    body: dict[str, Any] | None,
    error: type[SyncvitaError],
) -> None:
    if body is None:
        aioclient_mock.get(
            f"{API}/stats", status=status, text="<html>Bad Gateway</html>"
        )
    else:
        aioclient_mock.get(f"{API}/stats", status=status, json=body)
    with pytest.raises(SyncvitaError) as exc_info:
        await client.async_get_stats()
    assert type(exc_info.value) is error
    assert exc_info.value.status == status
    assert API_KEY not in str(exc_info.value)


async def test_validation_message_is_kept(
    client: SyncvitaClient, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(
        f"{API}/weight", status=400, json=_error("VALIDATION_ERROR", "weight too high")
    )
    with pytest.raises(SyncvitaValidationError, match="weight too high"):
        await client.async_post_measure("/weight", {"weight": 900})


async def test_rate_limit_error_has_retry_after(
    client: SyncvitaClient, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(
        f"{API}/stats",
        status=429,
        json=_error("RATE_LIMIT_EXCEEDED"),
        headers={"Retry-After": "120", "X-RateLimit-Remaining": "0"},
    )
    with pytest.raises(SyncvitaRateLimitError) as exc_info:
        await client.async_get_stats()
    assert exc_info.value.retry_after == 120
    assert client.rate_limit.remaining == 0


@pytest.mark.parametrize("exc", [aiohttp.ClientError(), TimeoutError()])
async def test_network_errors(
    client: SyncvitaClient, aioclient_mock: AiohttpClientMocker, exc: Exception
) -> None:
    aioclient_mock.get(f"{API}/stats", exc=exc)
    with pytest.raises(SyncvitaConnectionError) as exc_info:
        await client.async_get_stats()
    assert exc_info.value.status is None


async def test_rate_limit_headers_are_tracked(
    client: SyncvitaClient, aioclient_mock: AiohttpClientMocker
) -> None:
    reset = 1791612000
    aioclient_mock.get(
        f"{API}/stats",
        json={"success": True},
        headers={"X-RateLimit-Remaining": "7", "X-RateLimit-Reset": str(reset)},
    )
    await client.async_get_stats()
    reset_at = datetime.fromtimestamp(reset, UTC)
    assert client.rate_limit.remaining == 7
    assert client.rate_limit.reset_at == reset_at
    assert client.rate_limit.is_below(10, reset_at - timedelta(seconds=1))
    assert not client.rate_limit.is_below(10, reset_at)
    assert not client.rate_limit.is_below(5, reset_at - timedelta(seconds=1))


async def test_rate_limit_without_reset_never_pauses(client: SyncvitaClient) -> None:
    client.rate_limit.remaining = 0
    assert not client.rate_limit.is_below(10, datetime.now(UTC))


async def test_post_bulk(
    client: SyncvitaClient, aioclient_mock: AiohttpClientMocker
) -> None:
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
    entries = [
        {
            "type": "weight",
            "date": "2026-10-10",
            "time": "07:30:00",
            "values": {"weight": 80},
        }
    ]
    results = await client.async_post_bulk(entries)
    assert results == [
        BulkResult(index=0, created=True, error=None),
        BulkResult(index=1, created=False, error="out of range"),
    ]
    assert aioclient_mock.mock_calls[0][2] == {
        "data": entries,
        "options": {"continue_on_error": True},
    }


async def test_post_bulk_without_results(
    client: SyncvitaClient, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(f"{API}/bulk", status=201, json={"success": True})
    assert await client.async_post_bulk([]) == []
