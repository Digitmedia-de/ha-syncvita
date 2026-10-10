"""Coordinator reading the Apple Health export of one person."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    SyncvitaAuthError,
    SyncvitaClient,
    SyncvitaError,
    SyncvitaExportDisabledError,
)
from .const import (
    CONF_SCAN_INTERVAL,
    CONF_SLEEP_ENABLED,
    CONF_WORKOUTS_ENABLED,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENT_SLEEP_RECORDED,
    EVENT_WORKOUT_FINISHED,
    FULL_REFRESH_INTERVAL,
    METRIC_SLEEP,
    METRIC_WORKOUTS,
    READ_PAUSE_BELOW,
    SIGNAL_SENDER_UPDATED,
    WORKOUT_LOOKBACK_DAYS,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .models import SyncvitaConfigEntry
    from .store import SyncvitaStore

_LOGGER = logging.getLogger(__name__)


def parse_api_timestamp(value: Any) -> datetime | None:
    """Parse an API timestamp like '2026-10-10 06:12:03' (UTC)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace(" ", "T", 1))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _datetime(value: Any) -> datetime:
    moment = dt_util.parse_datetime(str(value))
    if moment is None:
        raise ValueError(f"invalid timestamp {value!r}")
    return moment


@dataclass(frozen=True, slots=True)
class MetricInfo:
    """One stored export type as listed by /health-export/metrics."""

    metric: str
    kind: str
    unit: str | None
    cumulative: bool
    count: int
    first_date: str | None
    last_date: str | None
    latest_value: float | None
    latest_date: str | None
    latest_time: str | None

    @classmethod
    def from_api(cls, raw: Mapping[str, Any]) -> MetricInfo:
        """Create from an API entry."""
        latest = raw.get("latest") or {}
        return cls(
            metric=str(raw["metric"]),
            kind=str(raw.get("kind", "quantity")),
            unit=raw.get("unit"),
            cumulative=bool(raw.get("cumulative")),
            count=int(raw.get("count") or 0),
            first_date=raw.get("first_date"),
            last_date=raw.get("last_date"),
            latest_value=_optional_float(latest.get("value")),
            latest_date=latest.get("date"),
            latest_time=latest.get("time"),
        )

    @property
    def signature(self) -> tuple[Any, ...]:
        """Values whose change means new data arrived."""
        return (
            self.count,
            self.last_date,
            self.latest_value,
            self.latest_date,
            self.latest_time,
        )

    @property
    def has_sensor(self) -> bool:
        """Return True for types that get a value sensor."""
        return self.kind == "quantity" and self.count > 0

    def latest_at(self) -> datetime | None:
        """Return the local time of the latest value."""
        if self.latest_date is None:
            return None
        moment = datetime.fromisoformat(
            f"{self.latest_date}T{self.latest_time or '00:00:00'}"
        )
        return moment.replace(tzinfo=dt_util.get_default_time_zone())


@dataclass(frozen=True, slots=True)
class SleepNight:
    """Sleep summary of one night, identified by the wake-up date."""

    night: str
    asleep_minutes: float
    stages: dict[str, float]
    bedtime: str | None
    wake_time: str | None

    @classmethod
    def from_api(cls, raw: Mapping[str, Any]) -> SleepNight:
        """Create from an API entry."""
        stages = raw.get("stages") or {}
        return cls(
            night=str(raw["date"]),
            asleep_minutes=float(raw["asleep_minutes"]),
            stages={
                str(name): float(minutes)
                for name, minutes in stages.items()
                if minutes is not None
            },
            bedtime=raw.get("bedtime"),
            wake_time=raw.get("wake_time"),
        )

    @property
    def duration_hours(self) -> float:
        """Return the sleep duration in hours."""
        return round(self.asleep_minutes / 60, 2)

    def event_data(self, entry_id: str) -> dict[str, Any]:
        """Return the payload of syncvita_sleep_recorded."""
        return {
            "config_entry_id": entry_id,
            "night": self.night,
            "duration_hours": self.duration_hours,
            "bedtime": self.bedtime,
            "wake_time": self.wake_time,
            "stages_minutes": self.stages,
        }


@dataclass(frozen=True, slots=True)
class Workout:
    """One workout from the export."""

    uuid: str
    activity_type: str
    start: datetime
    end: datetime
    duration_seconds: int
    energy_kcal: float | None
    distance_m: float | None
    avg_heart_rate: float | None
    max_heart_rate: float | None

    @classmethod
    def from_api(cls, raw: Mapping[str, Any]) -> Workout:
        """Create from an API entry (schema HealthExportWorkout)."""
        return cls(
            uuid=str(raw["uuid"]),
            activity_type=str(raw["activity_type"]),
            start=_datetime(raw["start"]),
            end=_datetime(raw["end"]),
            duration_seconds=int(raw["duration_seconds"]),
            energy_kcal=_optional_float(raw.get("energy_kcal")),
            distance_m=_optional_float(raw.get("distance_m")),
            avg_heart_rate=_optional_float(raw.get("avg_heart_rate")),
            max_heart_rate=_optional_float(raw.get("max_heart_rate")),
        )

    @property
    def duration_minutes(self) -> float:
        """Return the duration in minutes."""
        return round(self.duration_seconds / 60, 1)

    def event_data(self, entry_id: str) -> dict[str, Any]:
        """Return the payload of syncvita_workout_finished."""
        return {
            "config_entry_id": entry_id,
            "uuid": self.uuid,
            "activity_type": self.activity_type,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "duration_minutes": self.duration_minutes,
            "energy_kcal": self.energy_kcal,
            "distance_m": self.distance_m,
        }


@dataclass(slots=True)
class ExportData:
    """Everything the export entities show."""

    enabled: bool
    released_types: list[str] = field(default_factory=list)
    last_upload: datetime | None = None
    metrics: dict[str, MetricInfo] = field(default_factory=dict)
    sleep: SleepNight | None = None
    workouts: list[Workout] = field(default_factory=list)


class SyncvitaExportCoordinator(DataUpdateCoordinator[ExportData]):
    """Poll /metrics and load daily data only when something changed."""

    config_entry: SyncvitaConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: SyncvitaConfigEntry,
        client: SyncvitaClient,
        store: SyncvitaStore,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=timedelta(
                minutes=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self._client = client
        self._store = store
        self._settings: Mapping[str, Any] = {}
        self._settings_fetched: datetime | None = None
        self._signatures: dict[str, tuple[Any, ...]] = {}
        self._daily_fetched: dict[str, datetime] = {}
        # New workouts and nights are held back until the event entities exist
        # and Home Assistant has started (automations attach their triggers
        # only then). They are marked as seen only once they were fired.
        self._announcing = False
        self._pending_workouts: dict[str, Workout] = {}
        self._pending_night: SleepNight | None = None

    @callback
    def async_start_announcing(self) -> CALLBACK_TYPE:
        """Fire held back events once Home Assistant has started.

        Call after the platforms were set up. Returns a callback that cancels
        the wait (for entry.async_on_unload).
        """
        return async_at_started(self.hass, self._async_started)

    @callback
    def _async_started(self, _hass: HomeAssistant) -> None:
        self._announcing = True
        pending_workouts = sorted(
            self._pending_workouts.values(), key=lambda workout: workout.start
        )
        self._pending_workouts = {}
        if pending_workouts:
            self._async_fire_workouts(pending_workouts)
        night, self._pending_night = self._pending_night, None
        if night is not None:
            self._async_fire_sleep(night)

    async def _async_update_data(self) -> ExportData:
        now = dt_util.utcnow()
        if self.data is not None and self._client.rate_limit.is_below(
            READ_PAUSE_BELOW, now
        ):
            _LOGGER.debug("Few API requests left, the sending part goes first")
            return self.data
        try:
            data = await self._async_fetch(now)
        except SyncvitaAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SyncvitaExportDisabledError:
            data = ExportData(enabled=False)
        except SyncvitaError as err:
            raise UpdateFailed(f"Error talking to Syncvita: {err}") from err
        except (KeyError, TypeError, ValueError) as err:
            raise UpdateFailed(f"Unexpected export data: {err!r}") from err
        async_dispatcher_send(
            self.hass, SIGNAL_SENDER_UPDATED.format(self.config_entry.entry_id)
        )
        return data

    async def _async_fetch(self, now: datetime) -> ExportData:
        raw = await self._client.async_get_export_metrics()
        if not raw.get("enabled", True):
            return ExportData(enabled=False)
        metrics = {
            info.metric: info
            for info in (MetricInfo.from_api(entry) for entry in raw.get("metrics", []))
        }
        if (
            self._settings_fetched is None
            or now - self._settings_fetched >= FULL_REFRESH_INTERVAL
        ):
            self._settings = await self._client.async_get_export_settings()
            self._settings_fetched = now
        previous = self.data
        data = ExportData(
            enabled=True,
            released_types=list(self._settings.get("types", [])),
            last_upload=parse_api_timestamp(raw.get("last_upload_at")),
            metrics=metrics,
            sleep=previous.sleep if previous else None,
            workouts=previous.workouts if previous else [],
        )
        today = dt_util.now().date()
        options = self.config_entry.options
        if options.get(CONF_SLEEP_ENABLED, True) and self._daily_due(
            METRIC_SLEEP, metrics, now
        ):
            daily = await self._client.async_get_export_daily(
                METRIC_SLEEP, today - timedelta(days=1), today
            )
            nights = [SleepNight.from_api(night) for night in daily.get("nights", [])]
            data.sleep = max(nights, key=lambda night: night.night, default=None)
            self._async_handle_sleep(data.sleep)
        if options.get(CONF_WORKOUTS_ENABLED, True) and self._daily_due(
            METRIC_WORKOUTS, metrics, now
        ):
            daily = await self._client.async_get_export_daily(
                METRIC_WORKOUTS,
                today - timedelta(days=WORKOUT_LOOKBACK_DAYS - 1),
                today,
            )
            data.workouts = sorted(
                (Workout.from_api(workout) for workout in daily.get("workouts", [])),
                key=lambda workout: workout.start,
                reverse=True,
            )
            self._async_handle_workouts(data.workouts)
        self._signatures = {name: info.signature for name, info in metrics.items()}
        if not self._store.export_initialized:
            self._store.mark_export_initialized()
        return data

    def _daily_due(
        self, metric: str, metrics: Mapping[str, MetricInfo], now: datetime
    ) -> bool:
        info = metrics.get(metric)
        if info is None or info.count == 0:
            return False
        last = self._daily_fetched.get(metric)
        if (
            self._signatures.get(metric) == info.signature
            and last is not None
            and now - last < FULL_REFRESH_INTERVAL
        ):
            return False
        self._daily_fetched[metric] = now
        return True

    @callback
    def _async_handle_workouts(self, workouts: list[Workout]) -> None:
        new = [
            w
            for w in reversed(workouts)
            if w.uuid not in self._store.seen_workouts
            and w.uuid not in self._pending_workouts
        ]
        if not new:
            return
        if not self._store.export_initialized:
            # First contact with this export: everything already there is old.
            self._store.add_seen_workouts(workout.uuid for workout in new)
        elif not self._announcing:
            self._pending_workouts.update((workout.uuid, workout) for workout in new)
        else:
            self._async_fire_workouts(new)

    @callback
    def _async_fire_workouts(self, workouts: list[Workout]) -> None:
        for workout in workouts:
            self.hass.bus.async_fire(
                EVENT_WORKOUT_FINISHED,
                workout.event_data(self.config_entry.entry_id),
            )
        self._store.add_seen_workouts(workout.uuid for workout in workouts)

    @callback
    def _async_handle_sleep(self, night: SleepNight | None) -> None:
        last = self._store.last_sleep_night
        if self._pending_night is not None:
            last = max(last or "", self._pending_night.night)
        if night is None or (last is not None and night.night <= last):
            return
        if not self._store.export_initialized:
            self._store.set_last_sleep_night(night.night)
        elif not self._announcing:
            self._pending_night = night
        else:
            self._async_fire_sleep(night)

    @callback
    def _async_fire_sleep(self, night: SleepNight) -> None:
        self.hass.bus.async_fire(
            EVENT_SLEEP_RECORDED, night.event_data(self.config_entry.entry_id)
        )
        self._store.set_last_sleep_night(night.night)
