"""Tests for the options, reauth and reconfigure flows."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING
from unittest.mock import patch

from homeassistant.const import CONF_API_KEY, CONF_NAME
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

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
    from collections.abc import Generator
    from unittest.mock import AsyncMock

    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.test_util.aiohttp import (
        AiohttpClientMocker,
    )

pytestmark = pytest.mark.usefixtures("mock_setup_entry", "flow_sensors")

NEW_KEY = "sv_new_key_9876543210"


@pytest.fixture(autouse=True)
def mock_unload_entry() -> Generator[AsyncMock]:
    """Skip the real unload; the mocked setup leaves no runtime data behind."""
    with patch(
        "custom_components.syncvita.async_unload_entry", return_value=True
    ) as mock:
        yield mock


def _uid(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


async def _loaded_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _open(hass: HomeAssistant, entry: MockConfigEntry, step: str) -> str:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == step
    return str(result["flow_id"])


async def test_options_sensors(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    entry = await _loaded_entry(hass)
    flow_id = await _open(hass, entry, "sensors")
    result = await hass.config_entries.options.async_configure(
        flow_id, {"weight": "sensor.scale_weight"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SENSORS] == {"weight": "sensor.scale_weight"}
    assert entry.options[CONF_MAX_AGE_HOURS] == 72
    await hass.async_block_till_done()
    assert mock_setup_entry.call_count == 2


async def test_options_sensors_invalid_unit(hass: HomeAssistant) -> None:
    entry = await _loaded_entry(hass)
    flow_id = await _open(hass, entry, "sensors")
    result = await hass.config_entries.options.async_configure(
        flow_id, {"weight": "sensor.scale_stone"}
    )
    assert result["errors"] == {"weight": "invalid_unit"}


async def test_options_sensors_shared(hass: HomeAssistant) -> None:
    other = MockConfigEntry(
        domain=DOMAIN,
        unique_id="other",
        options={CONF_SENSORS: {"body_fat": "sensor.scale_fat"}},
    )
    other.add_to_hass(hass)
    entry = await _loaded_entry(hass)
    flow_id = await _open(hass, entry, "sensors")
    result = await hass.config_entries.options.async_configure(
        flow_id, {"body_fat": "sensor.scale_fat"}
    )
    assert result["step_id"] == "sensors_confirm"
    result = await hass.config_entries.options.async_configure(flow_id, {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SENSORS] == {"body_fat": "sensor.scale_fat"}


async def test_options_sending(hass: HomeAssistant) -> None:
    entry = await _loaded_entry(hass)
    flow_id = await _open(hass, entry, "sending")
    result = await hass.config_entries.options.async_configure(
        flow_id, {CONF_NOTIFY_ERRORS: False, CONF_MAX_AGE_HOURS: 24}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_NOTIFY_ERRORS] is False
    assert entry.options[CONF_MAX_AGE_HOURS] == 24
    assert isinstance(entry.options[CONF_MAX_AGE_HOURS], int)


async def test_options_export(hass: HomeAssistant) -> None:
    entry = await _loaded_entry(hass)
    flow_id = await _open(hass, entry, "export")
    result = await hass.config_entries.options.async_configure(
        flow_id,
        {
            CONF_READ_ENABLED: True,
            CONF_SCAN_INTERVAL: 30,
            CONF_SLEEP_ENABLED: False,
            CONF_WORKOUTS_ENABLED: True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_READ_ENABLED] is True
    assert entry.options[CONF_SCAN_INTERVAL] == 30
    assert entry.options[CONF_SLEEP_ENABLED] is False


@pytest.mark.usefixtures("valid_key")
async def test_reauth_success(hass: HomeAssistant) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"] == {"name": "Tim"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: NEW_KEY}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == NEW_KEY
    assert entry.unique_id == _uid(NEW_KEY)


async def test_reauth_invalid_key(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(
        f"{API}/stats",
        status=401,
        json={"success": False, "error": {"code": "UNAUTHORIZED", "message": "x"}},
    )
    entry = make_entry()
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: NEW_KEY}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data[CONF_API_KEY] == API_KEY


async def test_reauth_key_of_other_person(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=_uid(NEW_KEY)).add_to_hass(hass)
    entry = make_entry()
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: NEW_KEY}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure_name_only(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Timmy"}
    )
    assert result["reason"] == "reconfigure_successful"
    assert entry.title == "Timmy"
    assert entry.data == {CONF_NAME: "Timmy", CONF_API_KEY: API_KEY}
    assert aioclient_mock.call_count == 0


@pytest.mark.usefixtures("valid_key")
async def test_reconfigure_new_key(hass: HomeAssistant) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Tim", CONF_API_KEY: NEW_KEY}
    )
    assert result["reason"] == "reconfigure_successful"
    assert entry.unique_id == _uid(NEW_KEY)


async def test_reconfigure_new_key_unreachable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{API}/stats", status=500, json={})
    entry = make_entry()
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Tim", CONF_API_KEY: NEW_KEY}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_reconfigure_key_of_other_person(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=_uid(NEW_KEY)).add_to_hass(hass)
    entry = make_entry()
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Tim", CONF_API_KEY: NEW_KEY}
    )
    assert result["reason"] == "already_configured"
