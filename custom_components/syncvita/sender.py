"""Deliver new sensor values to Syncvita exactly once."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components import persistent_notification
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HassJob,
    HomeAssistant,
    callback,
)
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
)
from homeassistant.util import dt as dt_util

from .api import (
    SyncvitaAuthError,
    SyncvitaClient,
    SyncvitaError,
    SyncvitaRateLimitError,
    SyncvitaValidationError,
)
from .const import (
    BULK_CHUNK_SIZE,
    CONF_MAX_AGE_HOURS,
    CONF_NOTIFY_ERRORS,
    CONF_SENSORS,
    DEFAULT_MAX_AGE_HOURS,
    DEFAULT_NOTIFY_ERRORS,
    DOMAIN,
    RETRY_DELAYS_MINUTES,
    SEND_PAUSE_BELOW,
    SIGNAL_SENDER_UPDATED,
)
from .measures import MEASURES, Measure, Measurement, measurement_from_states
from .store import QueuedEntry, SyncvitaStore

if TYPE_CHECKING:
    from .models import SyncvitaConfigEntry

_LOGGER = logging.getLogger(__name__)


def queued_entry(source: str, measurement: Measurement) -> QueuedEntry:
    """Return the persisted form of a measurement."""
    return QueuedEntry(
        source=source,
        key=measurement.key,
        measure=measurement.measure.value,
        measured_at=measurement.measured_at.isoformat(),
        date=measurement.date,
        time=measurement.time,
        values=dict(measurement.values),
    )


def _bulk_entry(entry: QueuedEntry) -> dict[str, Any]:
    return {
        "type": MEASURES[Measure(entry["measure"])].bulk_type,
        "date": entry["date"],
        "time": entry["time"],
        "values": entry["values"],
    }


def _local(moment: datetime) -> str:
    return dt_util.as_local(moment).strftime("%Y-%m-%d %H:%M")


class SyncvitaSender:
    """Listen to the mapped sensors and send each new value once."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: SyncvitaConfigEntry,
        client: SyncvitaClient,
        store: SyncvitaStore,
    ) -> None:
        """Initialize the sender."""
        self._hass = hass
        self._entry = entry
        self._client = client
        self._store = store
        self._lock = asyncio.Lock()
        self._retry_step = 0
        self._cancel_timer: CALLBACK_TYPE | None = None
        self._timer_job = HassJob(self._async_timer_fired, cancel_on_shutdown=True)
        self._mapping = {
            entity_id: Measure(measure)
            for measure, entity_id in entry.options.get(CONF_SENSORS, {}).items()
        }
        self.last_error: str | None = None
        self.last_error_status: int | None = None

    @property
    def signal(self) -> str:
        """Dispatcher signal sent whenever the sender state changes."""
        return SIGNAL_SENDER_UPDATED.format(self._entry.entry_id)

    @property
    def _notification_id(self) -> str:
        return f"{DOMAIN}_{self._entry.entry_id}"

    @callback
    def async_start(self) -> None:
        """Start listening and process the persisted queue."""
        if self._mapping:
            self._entry.async_on_unload(
                async_track_state_change_event(
                    self._hass, list(self._mapping), self._async_state_changed
                )
            )
        self._entry.async_on_unload(self._async_cancel_timer)
        self._entry.async_create_background_task(
            self._hass, self.async_process_queue(), f"{DOMAIN}_process_queue"
        )

    @callback
    def _async_state_changed(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        measurement = measurement_from_states(
            self._mapping[entity_id], event.data["old_state"], event.data["new_state"]
        )
        if measurement is not None:
            self._entry.async_create_background_task(
                self._hass,
                self.async_submit(entity_id, measurement),
                f"{DOMAIN}_send_{entity_id}",
            )

    async def async_submit(self, source: str, measurement: Measurement) -> bool:
        """Send one value; queue it on temporary errors. True if the API took it."""
        async with self._lock:
            if self._store.is_known(source, measurement.key):
                return False
            if self._rate_limited():
                self._async_enqueue(source, measurement)
                self._async_schedule_rate_limit_resume()
                return False
            try:
                await self._client.async_post_measure(
                    MEASURES[measurement.measure].path, measurement.body()
                )
            except SyncvitaValidationError as err:
                self._async_report_rejected(measurement, err)
                return False
            except SyncvitaAuthError as err:
                self._async_enqueue(source, measurement)
                self._async_report_error(err)
                self._entry.async_start_reauth(self._hass)
                return False
            except SyncvitaError as err:
                self._async_enqueue(source, measurement)
                self._async_report_error(err)
                self._async_schedule_retry(err)
                return False
            self._store.mark_sent(source, measurement.key, dt_util.utcnow())
            self._async_report_success()
        if self._store.queue:
            await self.async_process_queue()
        return True

    async def async_process_queue(self) -> None:
        """Send queued values via /bulk in chunks of 100."""
        async with self._lock:
            self._async_drop_expired()
            if not self._store.queue:
                async_dispatcher_send(self._hass, self.signal)
                return
            if self._rate_limited():
                self._async_schedule_rate_limit_resume()
                return
            rejected = False
            while self._store.queue:
                chunk = self._store.queue[:BULK_CHUNK_SIZE]
                try:
                    await self._async_post_chunk(chunk)
                except SyncvitaValidationError as err:
                    rejected = True
                    _LOGGER.error(
                        "Syncvita rejected %d queued values: %s", len(chunk), err
                    )
                    self._async_report_error(err)
                    self._async_notify(
                        f"Syncvita hat {len(chunk)} nachgereichte Werte "
                        f"abgelehnt: {err}"
                    )
                except SyncvitaAuthError as err:
                    self._async_report_error(err)
                    self._entry.async_start_reauth(self._hass)
                    return
                except SyncvitaError as err:
                    self._async_report_error(err)
                    self._async_schedule_retry(err)
                    return
                del self._store.queue[: len(chunk)]
                self._store.async_schedule_save()
            if not rejected:
                self._async_report_success()

    async def async_send_bulk(self, entries: list[QueuedEntry]) -> tuple[int, int]:
        """Send entries via /bulk without queueing them; return (sent, rejected)."""
        sent = 0
        async with self._lock:
            for start in range(0, len(entries), BULK_CHUNK_SIZE):
                chunk = entries[start : start + BULK_CHUNK_SIZE]
                sent += sum(await self._async_post_chunk(chunk))
        if sent:
            self._async_report_success()
        return sent, len(entries) - sent

    async def _async_post_chunk(self, chunk: list[QueuedEntry]) -> list[bool]:
        results = await self._client.async_post_bulk([_bulk_entry(e) for e in chunk])
        failed = {
            result.index: result.error for result in results if not result.created
        }
        now = dt_util.utcnow()
        created: list[bool] = []
        for index, entry in enumerate(chunk):
            if index in failed:
                _LOGGER.warning(
                    "Syncvita rejected a queued %s value from %s %s: %s",
                    entry["measure"],
                    entry["date"],
                    entry["time"],
                    failed[index],
                )
                created.append(False)
            else:
                self._store.mark_sent(entry["source"], entry["key"], now)
                created.append(True)
        if failed:
            self._async_notify(
                f"Syncvita hat {len(failed)} nachgereichte Werte abgelehnt. "
                "Details stehen im Log."
            )
        return created

    def _rate_limited(self) -> bool:
        return self._client.rate_limit.is_below(SEND_PAUSE_BELOW, dt_util.utcnow())

    @callback
    def _async_enqueue(self, source: str, measurement: Measurement) -> None:
        self._store.queue.append(queued_entry(source, measurement))
        self._store.async_schedule_save()
        async_dispatcher_send(self._hass, self.signal)

    @callback
    def _async_drop_expired(self) -> None:
        hours = self._entry.options.get(CONF_MAX_AGE_HOURS, DEFAULT_MAX_AGE_HOURS)
        cutoff = dt_util.utcnow() - timedelta(hours=hours)
        expired = [
            entry
            for entry in self._store.queue
            if datetime.fromisoformat(entry["measured_at"]) < cutoff
        ]
        if not expired:
            return
        self._store.queue[:] = [e for e in self._store.queue if e not in expired]
        self._store.async_schedule_save()
        moments = sorted(datetime.fromisoformat(e["measured_at"]) for e in expired)
        _LOGGER.warning(
            "Dropped %d queued values older than %s hours", len(expired), hours
        )
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            f"expired_entries_{self._entry.entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="expired_entries",
            translation_placeholders={
                "name": self._entry.title,
                "count": str(len(expired)),
                "oldest": _local(moments[0]),
                "newest": _local(moments[-1]),
            },
        )

    @callback
    def _async_schedule_retry(self, err: SyncvitaError) -> None:
        if isinstance(err, SyncvitaRateLimitError) and err.retry_after is not None:
            delay = float(err.retry_after)
        else:
            step = min(self._retry_step, len(RETRY_DELAYS_MINUTES) - 1)
            delay = RETRY_DELAYS_MINUTES[step] * 60.0
            self._retry_step += 1
        self._async_schedule(delay)

    @callback
    def _async_schedule_rate_limit_resume(self) -> None:
        reset_at = self._client.rate_limit.reset_at
        delay = (reset_at - dt_util.utcnow()).total_seconds() if reset_at else 60.0
        self._async_schedule(max(delay, 1.0))

    @callback
    def _async_schedule(self, delay: float) -> None:
        self._async_cancel_timer()
        self._cancel_timer = async_call_later(self._hass, delay, self._timer_job)

    @callback
    def _async_cancel_timer(self) -> None:
        if self._cancel_timer is not None:
            self._cancel_timer()
            self._cancel_timer = None

    async def _async_timer_fired(self, _now: datetime) -> None:
        self._cancel_timer = None
        await self.async_process_queue()

    @callback
    def _async_report_success(self) -> None:
        if self.last_error is not None:
            _LOGGER.info("Sending to Syncvita works again")
        self.last_error = None
        self.last_error_status = None
        self._retry_step = 0
        persistent_notification.async_dismiss(self._hass, self._notification_id)
        async_dispatcher_send(self._hass, self.signal)

    @callback
    def _async_report_error(self, err: SyncvitaError) -> None:
        message = (str(err) or type(err).__name__)[:255]
        if self.last_error is None:
            _LOGGER.warning(
                "Sending to Syncvita failed (HTTP %s): %s", err.status, message
            )
        else:
            _LOGGER.debug(
                "Sending to Syncvita failed (HTTP %s): %s", err.status, message
            )
        self.last_error = message
        self.last_error_status = err.status
        async_dispatcher_send(self._hass, self.signal)

    @callback
    def _async_report_rejected(
        self, measurement: Measurement, err: SyncvitaValidationError
    ) -> None:
        self._async_report_error(err)
        _LOGGER.error(
            "Syncvita rejected %s value %s from %s %s: %s",
            measurement.measure,
            measurement.values,
            measurement.date,
            measurement.time,
            err,
        )
        value = measurement.values[MEASURES[measurement.measure].field]
        self._async_notify(
            f"Syncvita hat den Wert {value} ({measurement.measure}) vom "
            f"{measurement.date} {measurement.time} abgelehnt: {err}"
        )

    @callback
    def _async_notify(self, message: str) -> None:
        if self._entry.options.get(CONF_NOTIFY_ERRORS, DEFAULT_NOTIFY_ERRORS):
            persistent_notification.async_create(
                self._hass,
                message,
                title=f"Syncvita {self._entry.title}",
                notification_id=self._notification_id,
            )
