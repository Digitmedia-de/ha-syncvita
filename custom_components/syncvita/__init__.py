"""The Syncvita integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SyncvitaClient
from .const import CONF_READ_ENABLED, DOMAIN
from .coordinator import SyncvitaExportCoordinator
from .models import SyncvitaConfigEntry, SyncvitaRuntimeData
from .sender import SyncvitaSender
from .store import SyncvitaStore

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.typing import ConfigType

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Syncvita integration."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SyncvitaConfigEntry) -> bool:
    """Set up one Syncvita person from a config entry."""
    client = SyncvitaClient(async_get_clientsession(hass), entry.data[CONF_API_KEY])
    store = SyncvitaStore(hass, entry.entry_id)
    await store.async_load()
    coordinator: SyncvitaExportCoordinator | None = None
    if entry.options.get(CONF_READ_ENABLED, True):
        coordinator = SyncvitaExportCoordinator(hass, entry, client, store)
    sender = SyncvitaSender(hass, entry, client, store)
    entry.runtime_data = SyncvitaRuntimeData(
        client=client, store=store, sender=sender, coordinator=coordinator
    )
    if coordinator is not None:
        # Not async_config_entry_first_refresh: export problems must not stop
        # the sending part from queueing values (see README).
        await coordinator.async_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    sender.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SyncvitaConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.store.async_save()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: SyncvitaConfigEntry) -> None:
    """Delete the stored state when the entry is removed."""
    await SyncvitaStore(hass, entry.entry_id).async_remove()
