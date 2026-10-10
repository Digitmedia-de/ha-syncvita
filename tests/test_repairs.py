"""Tests for the repair issues."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er, issue_registry as ir
import pytest

from custom_components.syncvita.const import CONF_SENSORS, DOMAIN
from custom_components.syncvita.repairs import async_create_fix_flow

from .conftest import make_entry, setup_integration

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

pytestmark = pytest.mark.usefixtures("source_states")


def _issue_id(entry: MockConfigEntry, measure: str) -> str:
    return f"missing_sensor_{entry.entry_id}_{measure}"


async def test_missing_sensor_issue(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, mock_config_entry)
    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, _issue_id(mock_config_entry, "hydration"))
    assert (
        issues.async_get_issue(DOMAIN, _issue_id(mock_config_entry, "weight")) is None
    )


async def test_issue_when_sensor_removed_at_runtime(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    # A fresh object ID: sensor.scale_fat already has a state and would get "_2".
    entity_registry.async_get_or_create(
        "sensor", "test", "fat", suggested_object_id="registry_fat"
    )
    entry = make_entry(
        **{
            CONF_SENSORS: {
                "weight": "sensor.scale_weight",
                "body_fat": "sensor.registry_fat",
            }
        }
    )
    await setup_integration(hass, entry)
    issue_id = _issue_id(entry, "body_fat")
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    entity_registry.async_remove("sensor.registry_fat")
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None


async def _fix_flow(hass: HomeAssistant, entry: MockConfigEntry, measure: str):  # type: ignore[no-untyped-def]
    flow = await async_create_fix_flow(
        hass,
        _issue_id(entry, measure),
        {"entry_id": entry.entry_id, "measure": measure},
    )
    flow.hass = hass
    return flow


async def test_fix_flow_replaces_sensor(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, mock_config_entry)
    hass.states.async_set("sensor.water_new", "1.0", {"unit_of_measurement": "L"})
    hass.states.async_set("sensor.percent", "1.0", {"unit_of_measurement": "%"})
    flow = await _fix_flow(hass, mock_config_entry, "hydration")

    result = await flow.async_step_init()
    assert result["type"] is FlowResultType.FORM
    assert result["description_placeholders"]["entity_id"] == "sensor.water"

    result = await flow.async_step_init({"hydration": "sensor.percent"})
    assert result["errors"] == {"hydration": "invalid_unit"}

    result = await flow.async_step_init({"hydration": "sensor.water_new"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert mock_config_entry.options[CONF_SENSORS]["hydration"] == "sensor.water_new"
    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, _issue_id(mock_config_entry, "hydration")
        )
        is None
    )


async def test_fix_flow_can_drop_measure(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, mock_config_entry)
    flow = await _fix_flow(hass, mock_config_entry, "hydration")
    result = await flow.async_step_init({})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "hydration" not in mock_config_entry.options[CONF_SENSORS]


async def test_fix_flow_keeps_last_sensor(hass: HomeAssistant) -> None:
    entry = make_entry(**{CONF_SENSORS: {"hydration": "sensor.water"}})
    await setup_integration(hass, entry)
    flow = await _fix_flow(hass, entry, "hydration")
    result = await flow.async_step_init({})
    assert result["errors"] == {"base": "no_sensors"}


async def test_fix_flow_entry_removed(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, mock_config_entry)
    flow = await _fix_flow(hass, mock_config_entry, "hydration")
    await hass.config_entries.async_remove(mock_config_entry.entry_id)
    result = await flow.async_step_init()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "entry_removed"


async def test_issue_deleted_when_mapping_removed(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, mock_config_entry)
    issue_id = _issue_id(mock_config_entry, "hydration")
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    sensors = dict(mock_config_entry.options[CONF_SENSORS])
    del sensors["hydration"]
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={**mock_config_entry.options, CONF_SENSORS: sensors},
    )
    assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_issues_deleted_when_entry_removed(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, mock_config_entry)
    expired_id = f"expired_entries_{mock_config_entry.entry_id}"
    ir.async_create_issue(
        hass,
        DOMAIN,
        expired_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="expired_entries",
    )
    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, _issue_id(mock_config_entry, "hydration"))

    assert await hass.config_entries.async_remove(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert issues.async_get_issue(DOMAIN, expired_id) is None
    assert not [
        issue_id
        for (domain, issue_id) in issues.issues
        if domain == DOMAIN and mock_config_entry.entry_id in issue_id
    ]
