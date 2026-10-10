"""Tests for the user and sensor steps of the config flow."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import aiohttp
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_API_KEY, CONF_NAME
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.syncvita.config_flow import sensors_schema, syncvita_entity_ids
from custom_components.syncvita.const import (
    CONF_MAX_AGE_HOURS,
    CONF_NOTIFY_ERRORS,
    CONF_READ_ENABLED,
    CONF_SCAN_INTERVAL,
    CONF_SENSORS,
    CONF_SLEEP_ENABLED,
    CONF_WORKOUTS_ENABLED,
    DOMAIN,
)

from .conftest import API, API_KEY, make_entry

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.test_util.aiohttp import (
        AiohttpClientMocker,
    )

pytestmark = pytest.mark.usefixtures("mock_setup_entry", "flow_sensors")

UNIQUE_ID = hashlib.sha256(API_KEY.encode()).hexdigest()[:16]
ACCOUNT = {CONF_NAME: "Tim", CONF_API_KEY: API_KEY}


async def _to_sensor_step(hass: HomeAssistant) -> dict[str, Any]:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ACCOUNT)
    assert result["step_id"] == "sensors"
    return result


@pytest.mark.usefixtures("valid_key")
async def test_full_flow(hass: HomeAssistant) -> None:
    result = await _to_sensor_step(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"weight": "sensor.scale_weight", "body_fat": "sensor.scale_fat"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Tim"
    assert result["data"] == ACCOUNT
    assert result["options"] == {
        CONF_SENSORS: {"weight": "sensor.scale_weight", "body_fat": "sensor.scale_fat"},
        CONF_NOTIFY_ERRORS: True,
        CONF_MAX_AGE_HOURS: 72,
        CONF_READ_ENABLED: True,
        CONF_SCAN_INTERVAL: 15,
        CONF_SLEEP_ENABLED: True,
        CONF_WORKOUTS_ENABLED: True,
    }
    assert result["result"].unique_id == UNIQUE_ID


@pytest.mark.parametrize(
    "settings",
    [
        {
            "status": 403,
            "json": {
                "success": False,
                "error": {"code": "EXPORT_DISABLED", "message": "off"},
            },
        },
        {"status": 200, "json": {"success": True, "data": {"enabled": False}}},
    ],
)
async def test_export_off_disables_reading(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, settings: dict[str, Any]
) -> None:
    aioclient_mock.get(f"{API}/stats", json={"success": True})
    aioclient_mock.get(f"{API}/health-export/settings", **settings)
    result = await _to_sensor_step(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"weight": "sensor.scale_weight"}
    )
    assert result["options"][CONF_READ_ENABLED] is False


@pytest.mark.parametrize(
    "settings",
    [
        {"status": 404, "json": {}},
        {"status": 500, "json": {}},
        {"exc": aiohttp.ClientError()},
    ],
)
async def test_export_settings_failure_does_not_block(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, settings: dict[str, Any]
) -> None:
    aioclient_mock.get(f"{API}/stats", json={"success": True})
    aioclient_mock.get(f"{API}/health-export/settings", **settings)
    result = await _to_sensor_step(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"weight": "sensor.scale_weight"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_READ_ENABLED] is True


@pytest.mark.parametrize(
    ("mock_kwargs", "error"),
    [
        (
            {
                "status": 401,
                "json": {
                    "success": False,
                    "error": {"code": "UNAUTHORIZED", "message": "x"},
                },
            },
            "invalid_auth",
        ),
        ({"status": 500, "json": {}}, "cannot_connect"),
        ({"status": 429, "json": {}}, "cannot_connect"),
        ({"exc": aiohttp.ClientError()}, "cannot_connect"),
        ({"status": 404, "json": {}}, "unknown"),
    ],
)
async def test_user_errors_and_recovery(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_kwargs: dict[str, Any],
    error: str,
) -> None:
    aioclient_mock.get(f"{API}/stats", **mock_kwargs)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ACCOUNT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/stats", json={"success": True})
    aioclient_mock.get(
        f"{API}/health-export/settings",
        json={"success": True, "data": {"enabled": True}},
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ACCOUNT)
    assert result["step_id"] == "sensors"


async def test_unexpected_exception(hass: HomeAssistant) -> None:
    with patch(
        "custom_components.syncvita.config_flow.SyncvitaClient.async_get_stats",
        side_effect=RuntimeError("boom"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], ACCOUNT
        )
    assert result["errors"] == {"base": "unknown"}


async def test_already_configured(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=UNIQUE_ID, data=ACCOUNT).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ACCOUNT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.usefixtures("valid_key")
async def test_no_sensor_selected(hass: HomeAssistant) -> None:
    result = await _to_sensor_step(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "no_sensors"}


@pytest.mark.usefixtures("valid_key")
async def test_invalid_unit(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    entity_registry.async_get_or_create(
        "sensor",
        "test",
        "water",
        suggested_object_id="water_meter",
        unit_of_measurement="mL",
    )
    result = await _to_sensor_step(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "weight": "sensor.scale_stone",
            "body_fat": "sensor.ghost",
            "hydration": "sensor.water_meter",
        },
    )
    assert result["errors"] == {"weight": "invalid_unit", "body_fat": "invalid_unit"}


@pytest.mark.usefixtures("valid_key")
async def test_shared_sensor_needs_confirmation(hass: HomeAssistant) -> None:
    make_entry().add_to_hass(hass)
    result = await _to_sensor_step(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"weight": "sensor.scale_weight"}
    )
    assert result["step_id"] == "sensors_confirm"
    assert result["description_placeholders"] == {"entities": "sensor.scale_weight"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_syncvita_entities_are_excluded(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "x_metric_body_mass",
        suggested_object_id="syncvita_tim_weight",
    )
    assert syncvita_entity_ids(hass) == ["sensor.syncvita_tim_weight"]
    schema = sensors_schema(hass, {})
    selector = next(value for key, value in schema.schema.items() if key == "weight")
    assert selector.config["exclude_entities"] == ["sensor.syncvita_tim_weight"]
    assert selector.config["device_class"] == ["weight"]
