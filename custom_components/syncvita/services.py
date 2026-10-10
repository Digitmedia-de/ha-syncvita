"""Actions of the Syncvita integration."""

from __future__ import annotations

from itertools import pairwise
from typing import TYPE_CHECKING

from homeassistant.components.recorder import history
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_CONFIG_ENTRY_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    State,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.recorder import get_instance
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .api import SyncvitaError
from .const import CONF_SENSORS, DOMAIN
from .measures import (
    MEASURES,
    Measure,
    Measurement,
    OutOfRangeError,
    build_measurement,
    measurement_from_states,
)
from .sender import queued_entry

if TYPE_CHECKING:
    from datetime import datetime

    from .models import SyncvitaConfigEntry
    from .store import QueuedEntry

SERVICE_SEND_VALUE = "send_value"
SERVICE_BACKFILL = "backfill"
ATTR_MEASURE = "measure"
ATTR_VALUE = "value"
ATTR_MEASURED_AT = "measured_at"
ATTR_START = "start"
ATTR_MEASURES = "measures"
MEASURE_KEYS = [measure.value for measure in Measure]

SEND_VALUE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_MEASURE): vol.In(MEASURE_KEYS),
        vol.Required(ATTR_VALUE): vol.Coerce(float),
        vol.Optional(ATTR_MEASURED_AT): cv.datetime,
    }
)
BACKFILL_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_START): cv.date,
        vol.Optional(ATTR_MEASURES): vol.All(cv.ensure_list, [vol.In(MEASURE_KEYS)]),
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions."""
    hass.services.async_register(
        DOMAIN, SERVICE_SEND_VALUE, _async_send_value, schema=SEND_VALUE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_BACKFILL,
        _async_backfill,
        schema=BACKFILL_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )


def _loaded_entry(call: ServiceCall) -> SyncvitaConfigEntry:
    entry = call.hass.config_entries.async_get_entry(call.data[ATTR_CONFIG_ENTRY_ID])
    if (
        entry is None
        or entry.domain != DOMAIN
        or entry.state is not ConfigEntryState.LOADED
    ):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="entry_not_loaded"
        )
    return entry


async def _async_send_value(call: ServiceCall) -> None:
    entry = _loaded_entry(call)
    measure = Measure(call.data[ATTR_MEASURE])
    measured_at: datetime = call.data.get(ATTR_MEASURED_AT) or dt_util.now()
    if measured_at.tzinfo is None:
        measured_at = measured_at.replace(tzinfo=dt_util.get_default_time_zone())
    try:
        measurement = build_measurement(
            measure, call.data[ATTR_VALUE], MEASURES[measure].base_unit, measured_at
        )
    except OutOfRangeError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="out_of_range",
            translation_placeholders={
                "value": str(call.data[ATTR_VALUE]),
                "measure": measure.value,
            },
        ) from err
    await entry.runtime_data.sender.async_submit(
        f"service:{measure.value}", measurement
    )


def measurements_from_history(
    measure: Measure, states: list[State]
) -> list[Measurement]:
    """Apply the live guards to consecutive recorded states."""
    return [
        measurement
        for old, new in pairwise(states)
        if (measurement := measurement_from_states(measure, old, new)) is not None
    ]


def _load_history(hass: HomeAssistant, entity_id: str, start: datetime) -> list[State]:
    changes = history.state_changes_during_period(
        hass, start, entity_id=entity_id, include_start_time_state=True
    )
    return list(changes.get(entity_id, []))


async def _async_backfill(call: ServiceCall) -> ServiceResponse:
    entry = _loaded_entry(call)
    hass = call.hass
    sensors: dict[str, str] = entry.options.get(CONF_SENSORS, {})
    selected = call.data.get(ATTR_MEASURES) or list(sensors)
    start = dt_util.start_of_local_day(call.data[ATTR_START])
    store = entry.runtime_data.store
    pending: list[QueuedEntry] = []
    skipped = 0
    for key in selected:
        if (entity_id := sensors.get(key)) is None:
            continue
        states = await get_instance(hass).async_add_executor_job(
            _load_history, hass, entity_id, start
        )
        for measurement in measurements_from_history(Measure(key), states):
            if store.is_known(entity_id, measurement.key):
                skipped += 1
            else:
                pending.append(queued_entry(entity_id, measurement))
    try:
        sent, failed = await entry.runtime_data.sender.async_send_bulk(pending)
    except SyncvitaError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="send_failed",
            translation_placeholders={"error": str(err)},
        ) from err
    if not call.return_response:
        return None
    return {"sent": sent, "skipped": skipped, "failed": failed}
