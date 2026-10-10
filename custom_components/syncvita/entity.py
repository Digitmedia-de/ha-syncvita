"""Shared entity helpers of the Syncvita integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import DOMAIN

if TYPE_CHECKING:
    from .models import SyncvitaConfigEntry


def device_info(entry: SyncvitaConfigEntry) -> DeviceInfo:
    """Return the service device of one person."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"Syncvita {entry.title}",
        manufacturer="Syncvita",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url="https://syncvita.de",
    )
