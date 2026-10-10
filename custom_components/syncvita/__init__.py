"""The Syncvita integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SyncvitaClient
from .const import (
    CONF_READ_ENABLED,
    CONF_SLEEP_ENABLED,
    CONF_WORKOUTS_ENABLED,
    DOMAIN,
)
from .coordinator import SyncvitaExportCoordinator
from .models import SyncvitaConfigEntry, SyncvitaRuntimeData
from .repairs import async_delete_issues, async_setup_missing_sensor_checks
from .sender import SyncvitaSender
from .services import async_setup_services
from .store import SyncvitaStore
from .values import SyncvitaValuesCoordinator

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.typing import ConfigType

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.EVENT,
    Platform.SENSOR,
]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# Keys (unique ID without the entry ID prefix) of the entities per feature.
SLEEP_KEYS: frozenset[str] = frozenset({"sleep_last_night", "sleep"})
WORKOUT_KEYS: frozenset[str] = frozenset({"last_workout", "workouts_today", "workout"})
EXPORT_KEYS: frozenset[str] = frozenset(
    {"last_upload", "stored_values", "export_active", *SLEEP_KEYS, *WORKOUT_KEYS}
)
METRIC_KEY_PREFIX = "metric_"


def _disabled_keys(entry: SyncvitaConfigEntry) -> set[str]:
    options = entry.options
    if not options.get(CONF_READ_ENABLED, True):
        return set(EXPORT_KEYS)
    keys: set[str] = set()
    if not options.get(CONF_SLEEP_ENABLED, True):
        keys |= SLEEP_KEYS
    if not options.get(CONF_WORKOUTS_ENABLED, True):
        keys |= WORKOUT_KEYS
    return keys


@callback
def async_remove_disabled_entities(
    hass: HomeAssistant, entry: SyncvitaConfigEntry
) -> None:
    """Remove registry entries of features switched off in the options."""
    disabled = _disabled_keys(entry)
    if not disabled:
        return
    read_enabled = entry.options.get(CONF_READ_ENABLED, True)
    prefix = f"{entry.entry_id}_"
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        key = entity.unique_id.removeprefix(prefix)
        if key in disabled or (not read_enabled and key.startswith(METRIC_KEY_PREFIX)):
            registry.async_remove(entity.entity_id)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions of the integration."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SyncvitaConfigEntry) -> bool:
    """Set up one Syncvita person from a config entry."""
    client = SyncvitaClient(async_get_clientsession(hass), entry.data[CONF_API_KEY])
    store = SyncvitaStore(hass, entry.entry_id)
    await store.async_load()
    coordinator: SyncvitaExportCoordinator | None = None
    if entry.options.get(CONF_READ_ENABLED, True):
        coordinator = SyncvitaExportCoordinator(hass, entry, client, store)
    values = SyncvitaValuesCoordinator(hass, entry, client)
    sender = SyncvitaSender(hass, entry, client, store, values)
    async_remove_disabled_entities(hass, entry)
    entry.runtime_data = SyncvitaRuntimeData(
        client=client,
        store=store,
        sender=sender,
        values=values,
        coordinator=coordinator,
    )
    # Not async_config_entry_first_refresh: reading problems must not stop the
    # sending part from queueing values (see README).
    await values.async_refresh()
    if coordinator is not None:
        await coordinator.async_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if coordinator is not None:
        # Events found by the first refresh are fired only now, when the event
        # entities listen, and after Home Assistant has started.
        entry.async_on_unload(coordinator.async_start_announcing())
    sender.async_start()
    async_setup_missing_sensor_checks(hass, entry)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SyncvitaConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.store.async_save()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: SyncvitaConfigEntry) -> None:
    """Delete the stored state and the repair issues when the entry is removed."""
    async_delete_issues(hass, entry.entry_id)
    await SyncvitaStore(hass, entry.entry_id).async_remove()
