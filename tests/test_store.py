"""Tests for the persistent store."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from custom_components.syncvita.const import SENT_KEYS_PER_SOURCE
from custom_components.syncvita.store import QueuedEntry, SyncvitaStore

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

WHEN = datetime(2026, 10, 10, 7, 30, tzinfo=UTC)


def _queued(key: str) -> QueuedEntry:
    return QueuedEntry(
        source="sensor.scale_weight",
        key=key,
        measure="weight",
        measured_at=WHEN.isoformat(),
        date="2026-10-10",
        time="09:30:00",
        values={"weight": 80.0},
    )


async def test_roundtrip(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    store = SyncvitaStore(hass, "entry1")
    await store.async_load()
    store.mark_sent("sensor.scale_weight", "k1", WHEN)
    store.queue.append(_queued("k2"))
    store.add_seen_workouts(["w1"])
    store.set_last_sleep_night("2026-10-10")
    store.mark_export_initialized()
    await store.async_save()
    assert "syncvita.entry1" in hass_storage

    loaded = SyncvitaStore(hass, "entry1")
    await loaded.async_load()
    assert loaded.sent_keys == {"sensor.scale_weight": ["k1"]}
    assert loaded.sent_count == 1
    assert loaded.last_success == WHEN
    assert loaded.queue == [_queued("k2")]
    assert loaded.seen_workouts == ["w1"]
    assert loaded.last_sleep_night == "2026-10-10"
    assert loaded.export_initialized is True


async def test_empty_store_defaults(hass: HomeAssistant) -> None:
    store = SyncvitaStore(hass, "fresh")
    await store.async_load()
    assert store.queue == []
    assert store.sent_count == 0
    assert store.last_success is None
    assert store.export_initialized is False


async def test_is_known_checks_sent_and_queue(hass: HomeAssistant) -> None:
    store = SyncvitaStore(hass, "entry1")
    store.mark_sent("sensor.a", "k1", WHEN)
    store.queue.append(_queued("k2"))
    assert store.is_known("sensor.a", "k1")
    assert store.is_known("sensor.scale_weight", "k2")
    assert not store.is_known("sensor.b", "k1")
    assert not store.is_known("sensor.a", "k3")


async def test_sent_keys_are_capped(hass: HomeAssistant) -> None:
    store = SyncvitaStore(hass, "entry1")
    for index in range(SENT_KEYS_PER_SOURCE + 5):
        store.mark_sent("sensor.a", f"k{index}", WHEN)
    keys = store.sent_keys["sensor.a"]
    assert len(keys) == SENT_KEYS_PER_SOURCE
    assert keys[0] == "k5"


async def test_seen_workouts_are_unique(hass: HomeAssistant) -> None:
    store = SyncvitaStore(hass, "entry1")
    store.add_seen_workouts(["w1", "w2"])
    store.add_seen_workouts(["w2", "w3"])
    assert store.seen_workouts == ["w1", "w2", "w3"]


async def test_remove(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    store = SyncvitaStore(hass, "entry1")
    await store.async_save()
    await store.async_remove()
    assert "syncvita.entry1" not in hass_storage
