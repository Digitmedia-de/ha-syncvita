"""Diagnostics of the Syncvita integration (no health values)."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY, CONF_NAME

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .models import SyncvitaConfigEntry

TO_REDACT = {CONF_API_KEY, CONF_NAME, "title", "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SyncvitaConfigEntry
) -> dict[str, Any]:
    """Return type names, counts and timestamps only."""
    data = entry.runtime_data
    store = data.store
    moments = sorted(item["measured_at"] for item in store.queue)
    reset_at = data.client.rate_limit.reset_at
    diagnostics: dict[str, Any] = {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "sender": {
            "sent_count": store.sent_count,
            "last_success": store.last_success.isoformat()
            if store.last_success
            else None,
            "has_error": data.sender.last_error is not None,
            "last_error_status": data.sender.last_error_status,
            "sent_keys_per_source": {
                source: len(keys) for source, keys in store.sent_keys.items()
            },
        },
        "queue": {
            "size": len(store.queue),
            "per_measure": dict(Counter(item["measure"] for item in store.queue)),
            "oldest": moments[0] if moments else None,
            "newest": moments[-1] if moments else None,
        },
        "rate_limit": {
            "remaining": data.client.rate_limit.remaining,
            "reset_at": reset_at.isoformat() if reset_at else None,
        },
        "values": {
            "last_update_success": data.values.last_update_success,
            "types": {
                key: {
                    "total": reading.total,
                    "measured_at": reading.measured_at.isoformat()
                    if reading.measured_at
                    else None,
                }
                for key, reading in (data.values.data or {}).items()
            },
        },
        "export": None,
    }
    coordinator = data.coordinator
    if coordinator is not None and coordinator.data is not None:
        export = coordinator.data
        diagnostics["export"] = {
            "enabled": export.enabled,
            "last_update_success": coordinator.last_update_success,
            "released_types": export.released_types,
            "last_upload": export.last_upload.isoformat()
            if export.last_upload
            else None,
            "metrics": {
                name: {
                    "unit": info.unit,
                    "count": info.count,
                    "first_date": info.first_date,
                    "last_date": info.last_date,
                }
                for name, info in export.metrics.items()
            },
            "sleep_night": export.sleep.night if export.sleep else None,
            "workouts": len(export.workouts),
            "seen_workouts": len(store.seen_workouts),
        }
    return diagnostics
