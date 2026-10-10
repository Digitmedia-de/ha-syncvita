"""Persistent state of one Syncvita config entry."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, TypedDict

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import DOMAIN, SEEN_WORKOUTS_MAX, SENT_KEYS_PER_SOURCE

if TYPE_CHECKING:
    from collections.abc import Iterable

STORAGE_VERSION = 1
SAVE_DELAY_SECONDS = 1


class QueuedEntry(TypedDict):
    """A measurement waiting to be sent via /bulk."""

    source: str
    key: str
    measure: str
    measured_at: str
    date: str
    time: str
    values: dict[str, Any]


class SyncvitaStore:
    """Sent keys, queue, counters and export markers in .storage."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize an empty store."""
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}"
        )
        self.sent_keys: dict[str, list[str]] = {}
        self.queue: list[QueuedEntry] = []
        self.sent_count = 0
        self.last_success: datetime | None = None
        self.seen_workouts: list[str] = []
        self.last_sleep_night: str | None = None
        self.export_initialized = False

    async def async_load(self) -> None:
        """Load the persisted state."""
        data = await self._store.async_load() or {}
        self.sent_keys = data.get("sent_keys", {})
        self.queue = data.get("queue", [])
        self.sent_count = data.get("sent_count", 0)
        last_success = data.get("last_success")
        self.last_success = (
            datetime.fromisoformat(last_success) if last_success else None
        )
        self.seen_workouts = data.get("seen_workouts", [])
        self.last_sleep_night = data.get("last_sleep_night")
        self.export_initialized = data.get("export_initialized", False)

    def is_known(self, source: str, key: str) -> bool:
        """Return True if the value was already sent or is queued."""
        if key in self.sent_keys.get(source, ()):
            return True
        return any(
            entry["source"] == source and entry["key"] == key for entry in self.queue
        )

    def mark_sent(self, source: str, key: str, when: datetime) -> None:
        """Remember a value accepted by the API."""
        keys = self.sent_keys.setdefault(source, [])
        keys.append(key)
        del keys[:-SENT_KEYS_PER_SOURCE]
        self.sent_count += 1
        self.last_success = when
        self.async_schedule_save()

    def add_seen_workouts(self, uuids: Iterable[str]) -> None:
        """Remember workouts that were already announced."""
        for uuid in uuids:
            if uuid not in self.seen_workouts:
                self.seen_workouts.append(uuid)
        del self.seen_workouts[:-SEEN_WORKOUTS_MAX]
        self.async_schedule_save()

    def set_last_sleep_night(self, night: str) -> None:
        """Remember the newest announced night (wake-up date)."""
        self.last_sleep_night = night
        self.async_schedule_save()

    def mark_export_initialized(self) -> None:
        """Mark that the first export read is done; later reads fire events."""
        self.export_initialized = True
        self.async_schedule_save()

    @callback
    def async_schedule_save(self) -> None:
        """Save soon, coalescing several changes into one write."""
        self._store.async_delay_save(self._as_dict, SAVE_DELAY_SECONDS)

    async def async_save(self) -> None:
        """Save immediately."""
        await self._store.async_save(self._as_dict())

    async def async_remove(self) -> None:
        """Delete the stored file."""
        await self._store.async_remove()

    def _as_dict(self) -> dict[str, Any]:
        return {
            "sent_keys": self.sent_keys,
            "queue": self.queue,
            "sent_count": self.sent_count,
            "last_success": self.last_success.isoformat()
            if self.last_success
            else None,
            "seen_workouts": self.seen_workouts,
            "last_sleep_night": self.last_sleep_night,
            "export_initialized": self.export_initialized,
        }
