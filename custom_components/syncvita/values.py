"""Coordinator reading the values stored in Syncvita (weight, steps and so on)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import SyncvitaAuthError, SyncvitaClient, SyncvitaError
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    FULL_REFRESH_INTERVAL,
    READ_PAUSE_BELOW,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from homeassistant.core import HomeAssistant

    from .models import SyncvitaConfigEntry

_LOGGER = logging.getLogger(__name__)


def _latest(_today: date) -> dict[str, str]:
    return {"limit": "1"}


@dataclass(frozen=True, slots=True)
class ValueType:
    """How one value type of /stats is read in detail.

    The GET endpoints differ in their query parameters: /weight and /steps
    return only pending values unless status=all is given, /heart-rate and
    /body-fat filter literally by any status given, /steps and /weight filter
    the day with date_from/date_to, the others with date.
    """

    key: str
    path: str
    field: str
    daily: bool
    params: Callable[[date], dict[str, str]]
    time_fields: tuple[str, ...] = ("time",)


VALUE_TYPES: tuple[ValueType, ...] = (
    ValueType(
        "weight",
        "/weight",
        "weight_kg",
        daily=False,
        params=lambda _today: {"status": "all", "limit": "1"},
    ),
    ValueType("body_fat", "/body-fat", "percentage", daily=False, params=_latest),
    ValueType("heart_rate", "/heart-rate", "bpm", daily=False, params=_latest),
    ValueType("blood_oxygen", "/blood-oxygen", "spo2", daily=False, params=_latest),
    ValueType("blood_sugar", "/blood-sugar", "glucose", daily=False, params=_latest),
    ValueType(
        "steps",
        "/steps",
        "steps",
        daily=True,
        params=lambda today: {
            "status": "all",
            "date_from": today.isoformat(),
            "date_to": today.isoformat(),
            "limit": "1000",
        },
        time_fields=("end_time", "start_time"),
    ),
    ValueType(
        "hydration",
        "/hydration",
        "milliliters",
        daily=True,
        params=lambda today: {"date": today.isoformat(), "limit": "100"},
    ),
)


@dataclass(frozen=True, slots=True)
class ValueReading:
    """Latest value (or today's total) of one value type."""

    value: float | None
    measured_at: datetime | None
    total: int
    day: date
    fetched: datetime


def _measured_at(item: Mapping[str, Any], time_fields: tuple[str, ...]) -> datetime:
    clock = next((item[name] for name in time_fields if item.get(name)), "00:00:00")
    moment = datetime.combine(
        date.fromisoformat(str(item["date"])), time.fromisoformat(str(clock))
    )
    return moment.replace(tzinfo=dt_util.get_default_time_zone())


class SyncvitaValuesCoordinator(DataUpdateCoordinator[dict[str, ValueReading]]):
    """Poll /stats and load a value type only when its number of entries changed."""

    config_entry: SyncvitaConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: SyncvitaConfigEntry, client: SyncvitaClient
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title} values",
            update_interval=timedelta(
                minutes=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self._client = client

    async def _async_update_data(self) -> dict[str, ValueReading]:
        now = dt_util.utcnow()
        if self.data is not None and self._client.rate_limit.is_below(
            READ_PAUSE_BELOW, now
        ):
            _LOGGER.debug("Few API requests left, the sending part goes first")
            return self.data
        try:
            return await self._async_fetch(now)
        except SyncvitaAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SyncvitaError as err:
            raise UpdateFailed(f"Error talking to Syncvita: {err}") from err
        except (KeyError, TypeError, ValueError) as err:
            raise UpdateFailed(f"Unexpected Syncvita data: {err!r}") from err

    async def _async_fetch(self, now: datetime) -> dict[str, ValueReading]:
        payload = await self._client.async_get_stats()
        stats = payload.get("data") or {}
        previous = self.data or {}
        today = dt_util.now().date()
        readings: dict[str, ValueReading] = {}
        for value_type in VALUE_TYPES:
            total = int((stats.get(value_type.key) or {}).get("total") or 0)
            if total == 0:
                continue
            last = previous.get(value_type.key)
            if (
                last is not None
                and last.total == total
                and (last.day == today or not value_type.daily)
                and now - last.fetched < FULL_REFRESH_INTERVAL
            ):
                readings[value_type.key] = last
                continue
            readings[value_type.key] = await self._async_read(
                value_type, total, today, now
            )
        return readings

    async def _async_read(
        self, value_type: ValueType, total: int, today: date, now: datetime
    ) -> ValueReading:
        items = await self._client.async_get_values(
            value_type.path, value_type.params(today)
        )
        # Values may come as strings (raw database columns).
        if value_type.daily:
            value: float | None = sum(float(item[value_type.field]) for item in items)
        else:
            value = float(items[0][value_type.field]) if items else None
        measured_at = (
            max(_measured_at(item, value_type.time_fields) for item in items)
            if items
            else None
        )
        return ValueReading(value, measured_at, total, today, now)
