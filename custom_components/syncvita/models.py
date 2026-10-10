"""Runtime data of the Syncvita integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .api import SyncvitaClient
    from .sender import SyncvitaSender
    from .store import SyncvitaStore


@dataclass(slots=True)
class SyncvitaRuntimeData:
    """Objects shared by all platforms of one config entry."""

    client: SyncvitaClient
    store: SyncvitaStore
    sender: SyncvitaSender


type SyncvitaConfigEntry = ConfigEntry[SyncvitaRuntimeData]
