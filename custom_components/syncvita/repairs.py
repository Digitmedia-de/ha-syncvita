"""Repair issues of the Syncvita integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.helpers.start import async_at_started
import voluptuous as vol

from .config_flow import sensor_selector, validate_sensors
from .const import CONF_SENSORS, DOMAIN
from .measures import Measure

if TYPE_CHECKING:
    from homeassistant import data_entry_flow

    from .models import SyncvitaConfigEntry


def _issue_id(entry_id: str, measure: str) -> str:
    return f"missing_sensor_{entry_id}_{measure}"


@callback
def async_delete_issues(hass: HomeAssistant, entry_id: str) -> None:
    """Delete every repair issue of a config entry."""
    for measure in Measure:
        ir.async_delete_issue(hass, DOMAIN, _issue_id(entry_id, measure.value))
    ir.async_delete_issue(hass, DOMAIN, f"expired_entries_{entry_id}")


@callback
def async_check_missing_sensors(
    hass: HomeAssistant, entry: SyncvitaConfigEntry, removed: str | None = None
) -> None:
    """Create or delete one issue per mapped sensor that no longer exists."""
    registry = er.async_get(hass)
    sensors: dict[str, str] = entry.options.get(CONF_SENSORS, {})
    for unmapped in Measure:
        if unmapped.value not in sensors:
            ir.async_delete_issue(
                hass, DOMAIN, _issue_id(entry.entry_id, unmapped.value)
            )
    for measure, entity_id in sensors.items():
        issue_id = _issue_id(entry.entry_id, measure)
        missing = entity_id == removed or (
            registry.async_get(entity_id) is None and hass.states.get(entity_id) is None
        )
        if not missing:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
            continue
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="missing_sensor",
            translation_placeholders={"name": entry.title, "entity_id": entity_id},
            data={"entry_id": entry.entry_id, "measure": measure},
        )


@callback
def async_setup_missing_sensor_checks(
    hass: HomeAssistant, entry: SyncvitaConfigEntry
) -> None:
    """Check after startup and whenever an entity is removed."""

    @callback
    def _async_started(_hass: HomeAssistant) -> None:
        async_check_missing_sensors(hass, entry)

    @callback
    def _is_removal(event_data: er.EventEntityRegistryUpdatedData) -> bool:
        return event_data["action"] == "remove"

    @callback
    def _async_removed(event: Event[er.EventEntityRegistryUpdatedData]) -> None:
        async_check_missing_sensors(hass, entry, event.data["entity_id"])

    entry.async_on_unload(async_at_started(hass, _async_started))
    entry.async_on_unload(
        hass.bus.async_listen(
            er.EVENT_ENTITY_REGISTRY_UPDATED, _async_removed, event_filter=_is_removal
        )
    )


class MissingSensorRepairFlow(RepairsFlow):
    """Pick a replacement for a missing source sensor."""

    def __init__(self, entry_id: str, measure: Measure) -> None:
        """Initialize the flow."""
        self._entry_id = entry_id
        self._measure = measure

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Ask for the replacement sensor."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return self.async_abort(reason="entry_removed")
        key = self._measure.value
        sensors: dict[str, str] = dict(entry.options.get(CONF_SENSORS, {}))
        missing = sensors.get(key, "")
        errors: dict[str, str] = {}
        if user_input is not None:
            sensors.pop(key, None)
            if replacement := user_input.get(key):
                checked, errors = validate_sensors(self.hass, {key: replacement})
                sensors.update(checked)
            elif not sensors:
                errors = {"base": "no_sensors"}
            if not errors:
                self.hass.config_entries.async_update_entry(
                    entry, options={**entry.options, CONF_SENSORS: sensors}
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {vol.Optional(key): sensor_selector(self.hass, self._measure)}
            ),
            errors=errors,
            description_placeholders={"entity_id": missing, "name": entry.title},
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the fix flow of a missing_sensor issue."""
    assert data is not None
    return MissingSensorRepairFlow(str(data["entry_id"]), Measure(str(data["measure"])))
