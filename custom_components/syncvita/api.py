"""Client for the Syncvita API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import aiohttp

from .const import API_BASE_URL, API_TIMEOUT_SECONDS, EXPORT_API_VERSION

if TYPE_CHECKING:
    from multidict import CIMultiDictProxy


class SyncvitaError(Exception):
    """Base error of the Syncvita API."""

    def __init__(self, message: str = "", status: int | None = None) -> None:
        """Store the message and the HTTP status."""
        super().__init__(message)
        self.status = status


class SyncvitaConnectionError(SyncvitaError):
    """Network problem, timeout or server error (5xx)."""


class SyncvitaAuthError(SyncvitaError):
    """The API key is invalid (401)."""


class SyncvitaValidationError(SyncvitaError):
    """The API rejected the request body (400)."""


class SyncvitaExportDisabledError(SyncvitaError):
    """The Apple Health export is not enabled for this key (403 EXPORT_DISABLED)."""


class SyncvitaRateLimitError(SyncvitaError):
    """Too many requests (429)."""

    def __init__(self, message: str, status: int, retry_after: int | None) -> None:
        """Store the Retry-After value in seconds."""
        super().__init__(message, status)
        self.retry_after = retry_after


@dataclass(slots=True)
class RateLimitState:
    """Rate limit as reported by the last response."""

    remaining: int | None = None
    reset_at: datetime | None = None

    def is_below(self, threshold: int, now: datetime) -> bool:
        """Return True while fewer than threshold requests remain before reset."""
        return (
            self.remaining is not None
            and self.remaining < threshold
            and self.reset_at is not None
            and now < self.reset_at
        )


@dataclass(frozen=True, slots=True)
class BulkResult:
    """Result of one entry of a bulk upload."""

    index: int
    created: bool
    error: str | None


def _int_header(headers: CIMultiDictProxy[str], name: str) -> int | None:
    value = headers.get(name)
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


class SyncvitaClient:
    """Async client for one Syncvita API key."""

    def __init__(self, session: aiohttp.ClientSession, api_key: str) -> None:
        """Initialize the client."""
        self._session = session
        self._api_key = api_key
        self.rate_limit = RateLimitState()

    async def async_get_stats(self) -> dict[str, Any]:
        """Return the statistics; used to validate a key."""
        return await self._request("GET", "/stats")

    async def async_post_measure(self, path: str, body: dict[str, Any]) -> None:
        """Send one measurement to a single-value endpoint."""
        await self._request("POST", path, json=body)

    async def async_post_bulk(self, entries: list[dict[str, Any]]) -> list[BulkResult]:
        """Send up to 100 entries, continuing on per-entry errors."""
        payload = await self._request(
            "POST",
            "/bulk",
            json={"data": entries, "options": {"continue_on_error": True}},
        )
        data = payload.get("data")
        results = data.get("results", []) if isinstance(data, dict) else []
        return [
            BulkResult(
                index=int(result["index"]),
                created=result.get("status") == "created",
                error=result.get("error"),
            )
            for result in results
        ]

    async def async_get_export_metrics(self) -> dict[str, Any]:
        """Return the stored export types."""
        return await self._export_data("/health-export/metrics")

    async def async_get_export_settings(self) -> dict[str, Any]:
        """Return the export settings."""
        return await self._export_data("/health-export/settings")

    async def async_get_export_daily(
        self, metric: str, start: date, end: date
    ) -> dict[str, Any]:
        """Return daily values of one export type between start and end."""
        params = {"metric": metric, "from": start.isoformat(), "to": end.isoformat()}
        return await self._export_data("/health-export/daily", params)

    async def _export_data(
        self, path: str, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        payload = await self._request("GET", path, params=params, export=True)
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        export: bool = False,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }
        if export:
            headers["X-API-Version"] = EXPORT_API_VERSION
        try:
            async with self._session.request(
                method,
                f"{API_BASE_URL}{path}",
                json=json,
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT_SECONDS),
            ) as response:
                status = response.status
                response_headers = response.headers
                payload = await self._read_json(response)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SyncvitaConnectionError(type(err).__name__) from err

        self._update_rate_limit(response_headers)
        if status in (200, 201):
            return payload
        error = payload.get("error")
        error = error if isinstance(error, dict) else {}
        message = str(error.get("message") or f"HTTP {status}")
        code = error.get("code")
        if status == 400:
            raise SyncvitaValidationError(message, status)
        if status == 401:
            raise SyncvitaAuthError(message, status)
        if status == 403 and code == "EXPORT_DISABLED":
            raise SyncvitaExportDisabledError(message, status)
        if status == 429:
            raise SyncvitaRateLimitError(
                message, status, _int_header(response_headers, "Retry-After")
            )
        if status >= 500:
            raise SyncvitaConnectionError(message, status)
        raise SyncvitaError(message, status)

    @staticmethod
    async def _read_json(response: aiohttp.ClientResponse) -> dict[str, Any]:
        try:
            payload = await response.json(content_type=None)
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _update_rate_limit(self, headers: CIMultiDictProxy[str]) -> None:
        remaining = _int_header(headers, "X-RateLimit-Remaining")
        reset = _int_header(headers, "X-RateLimit-Reset")
        if remaining is not None:
            self.rate_limit.remaining = remaining
        if reset is not None:
            self.rate_limit.reset_at = datetime.fromtimestamp(reset, UTC)
