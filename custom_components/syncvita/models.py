"""Runtime data of the Syncvita integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .api import SyncvitaClient
    from .coordinator import SyncvitaExportCoordinator
    from .sender import SyncvitaSender
    from .store import SyncvitaStore
    from .values import SyncvitaValuesCoordinator


@dataclass(slots=True)
class SyncvitaRuntimeData:
    """Objects shared by all platforms of one config entry."""

    client: SyncvitaClient
    store: SyncvitaStore
    sender: SyncvitaSender
    values: SyncvitaValuesCoordinator
    coordinator: SyncvitaExportCoordinator | None = None


type SyncvitaConfigEntry = ConfigEntry[SyncvitaRuntimeData]
