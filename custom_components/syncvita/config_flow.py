"""Config flow for the Syncvita integration."""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, CONF_API_KEY, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import (
    SyncvitaAuthError,
    SyncvitaClient,
    SyncvitaConnectionError,
    SyncvitaExportDisabledError,
    SyncvitaRateLimitError,
)
from .const import (
    CONF_MAX_AGE_HOURS,
    CONF_NOTIFY_ERRORS,
    CONF_READ_ENABLED,
    CONF_SCAN_INTERVAL,
    CONF_SENSORS,
    CONF_SLEEP_ENABLED,
    CONF_WORKOUTS_ENABLED,
    DEFAULT_MAX_AGE_HOURS,
    DEFAULT_NOTIFY_ERRORS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .measures import MEASURES, Measure

if TYPE_CHECKING:
    from collections.abc import Mapping

_LOGGER = logging.getLogger(__name__)

API_KEY_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): TextSelector(),
        vol.Required(CONF_API_KEY): API_KEY_SELECTOR,
    }
)


def unique_id_for_key(api_key: str) -> str:
    """Return the unique ID of an API key without storing the key itself."""
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


def default_options(*, read_enabled: bool) -> dict[str, Any]:
    """Return the options of a new entry, without the sensor mapping."""
    return {
        CONF_NOTIFY_ERRORS: DEFAULT_NOTIFY_ERRORS,
        CONF_MAX_AGE_HOURS: DEFAULT_MAX_AGE_HOURS,
        CONF_READ_ENABLED: read_enabled,
        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
        CONF_SLEEP_ENABLED: True,
        CONF_WORKOUTS_ENABLED: True,
    }


@callback
def syncvita_entity_ids(hass: HomeAssistant) -> list[str]:
    """Return all entities of this integration (never offered as a source)."""
    return sorted(
        entry.entity_id
        for entry in er.async_get(hass).entities.values()
        if entry.platform == DOMAIN
    )


@callback
def sensor_selector(hass: HomeAssistant, measure: Measure) -> EntitySelector:
    """Return the entity selector for one measure."""
    exclude = syncvita_entity_ids(hass)
    device_class = MEASURES[measure].device_class
    if device_class is None:
        config = EntitySelectorConfig(domain=SENSOR_DOMAIN, exclude_entities=exclude)
    else:
        config = EntitySelectorConfig(
            domain=SENSOR_DOMAIN, device_class=device_class, exclude_entities=exclude
        )
    return EntitySelector(config)


@callback
def sensors_schema(hass: HomeAssistant, current: Mapping[str, Any]) -> vol.Schema:
    """Return the schema with one optional selector per measure."""
    return vol.Schema(
        {
            vol.Optional(
                measure.value,
                description={"suggested_value": current.get(measure.value)},
            ): sensor_selector(hass, measure)
            for measure in Measure
        }
    )


@callback
def entity_unit(hass: HomeAssistant, entity_id: str) -> str | None:
    """Return the unit of an entity from its state or the entity registry."""
    if (state := hass.states.get(entity_id)) is not None:
        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        return str(unit) if unit is not None else None
    if (entry := er.async_get(hass).async_get(entity_id)) is not None:
        return entry.unit_of_measurement
    return None


@callback
def validate_sensors(
    hass: HomeAssistant, user_input: Mapping[str, Any]
) -> tuple[dict[str, str], dict[str, str]]:
    """Return the selected mapping and errors (no_sensors, invalid_unit)."""
    sensors = {key: str(value) for key, value in user_input.items() if value}
    if not sensors:
        return sensors, {"base": "no_sensors"}
    errors = {
        key: "invalid_unit"
        for key, entity_id in sensors.items()
        if entity_unit(hass, entity_id) not in MEASURES[Measure(key)].units
    }
    return sensors, errors


@callback
def shared_sensors(
    hass: HomeAssistant, sensors: Mapping[str, str], own_entry_id: str | None
) -> list[str]:
    """Return sensors that already send to another Syncvita entry."""
    used = {
        entity_id
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.entry_id != own_entry_id
        for entity_id in entry.options.get(CONF_SENSORS, {}).values()
    }
    return sorted(used & set(sensors.values()))


async def async_validate_api_key(
    hass: HomeAssistant, api_key: str
) -> tuple[dict[str, str], bool]:
    """Check a key via /stats; return errors and whether the export is enabled."""
    client = SyncvitaClient(async_get_clientsession(hass), api_key)
    try:
        await client.async_get_stats()
        try:
            settings = await client.async_get_export_settings()
        except SyncvitaExportDisabledError:
            return {}, False
    except SyncvitaAuthError:
        return {"base": "invalid_auth"}, False
    except SyncvitaConnectionError, SyncvitaRateLimitError:
        return {"base": "cannot_connect"}, False
    except Exception:
        _LOGGER.exception("Unexpected error while checking the Syncvita API key")
        return {"base": "unknown"}, False
    return {}, bool(settings.get("enabled"))


class SyncvitaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Syncvita."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._name = ""
        self._api_key = ""
        self._read_enabled = True
        self._sensors: dict[str, str] = {}
        self._shared: list[str] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the person's name and API key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(unique_id_for_key(user_input[CONF_API_KEY]))
            self._abort_if_unique_id_configured()
            errors, self._read_enabled = await async_validate_api_key(
                self.hass, user_input[CONF_API_KEY]
            )
            if not errors:
                self._name = user_input[CONF_NAME]
                self._api_key = user_input[CONF_API_KEY]
                return await self.async_step_sensors()
        suggested = {CONF_NAME: user_input[CONF_NAME]} if user_input else {}
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, suggested
            ),
            errors=errors,
        )

    async def async_step_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Map source sensors to measures."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._sensors, errors = validate_sensors(self.hass, user_input)
            if not errors:
                self._shared = shared_sensors(self.hass, self._sensors, None)
                if self._shared:
                    return await self.async_step_sensors_confirm()
                return self._async_create()
        return self.async_show_form(
            step_id="sensors",
            data_schema=sensors_schema(self.hass, user_input or {}),
            errors=errors,
        )

    async def async_step_sensors_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Warn about sensors that already send to another person."""
        if user_input is not None:
            return self._async_create()
        return self.async_show_form(
            step_id="sensors_confirm",
            description_placeholders={"entities": ", ".join(self._shared)},
        )

    @callback
    def _async_create(self) -> ConfigFlowResult:
        return self.async_create_entry(
            title=self._name,
            data={CONF_NAME: self._name, CONF_API_KEY: self._api_key},
            options={
                **default_options(read_enabled=self._read_enabled),
                CONF_SENSORS: self._sensors,
            },
        )
