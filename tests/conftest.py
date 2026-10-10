"""Shared fixtures for the Syncvita tests."""

from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

from homeassistant.const import CONF_API_KEY, CONF_NAME
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

if TYPE_CHECKING:
    from collections.abc import Generator

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.test_util.aiohttp import (
        AiohttpClientMocker,
    )

pytest_plugins = "pytest_homeassistant_custom_component"

API = "https://api.syncvita.de/v1"
API_KEY = "sv_test_0123456789abcdef"
FIXTURES = Path(__file__).parent / "fixtures"
WEIGHT_ATTRS = {"unit_of_measurement": "kg", "device_class": "weight"}
FAT_ATTRS = {"unit_of_measurement": "%"}
WATER_ATTRS = {"unit_of_measurement": "L"}


def load_fixture(name: str) -> Any:
    """Load a JSON fixture from tests/fixtures."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_entry(**option_overrides: Any) -> MockConfigEntry:
    """Return a config entry for the person 'Tim'."""
    options: dict[str, Any] = {
        CONF_SENSORS: {
            "weight": "sensor.scale_weight",
            "body_fat": "sensor.scale_fat",
            "hydration": "sensor.water",
        },
        CONF_NOTIFY_ERRORS: True,
        CONF_MAX_AGE_HOURS: 72,
        CONF_READ_ENABLED: False,
        CONF_SCAN_INTERVAL: 15,
        CONF_SLEEP_ENABLED: True,
        CONF_WORKOUTS_ENABLED: True,
    }
    options.update(option_overrides)
    return MockConfigEntry(
        domain=DOMAIN,
        title="Tim",
        unique_id="0123456789abcdef",
        data={CONF_NAME: "Tim", CONF_API_KEY: API_KEY},
        options=options,
    )


async def setup_integration(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up the integration with Berlin as local time zone."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow loading custom_components in every test."""


@pytest.fixture
async def berlin(hass: HomeAssistant) -> None:
    """Use Europe/Berlin as local time zone."""
    await hass.config.async_set_time_zone("Europe/Berlin")


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Entry with the sending part only (export reading disabled)."""
    return make_entry()


@pytest.fixture
def export_config_entry() -> MockConfigEntry:
    """Entry with export reading enabled."""
    return make_entry(**{CONF_READ_ENABLED: True})


@pytest.fixture
def source_states(hass: HomeAssistant) -> None:
    """Initial states of the mapped source sensors (sensor.water stays missing)."""
    hass.states.async_set("sensor.scale_weight", "80.0", WEIGHT_ATTRS)
    hass.states.async_set("sensor.scale_fat", "20.0", FAT_ATTRS)


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Skip the real setup when a flow creates or reloads an entry."""
    with patch(
        "custom_components.syncvita.async_setup_entry", return_value=True
    ) as mock:
        yield mock


@pytest.fixture
def flow_sensors(hass: HomeAssistant) -> None:
    """Candidate sensors for the sensor step."""
    hass.states.async_set("sensor.scale_weight", "80.0", WEIGHT_ATTRS)
    hass.states.async_set("sensor.scale_fat", "20.0", FAT_ATTRS)
    hass.states.async_set(
        "sensor.scale_stone",
        "12.6",
        {"unit_of_measurement": "st", "device_class": "weight"},
    )


@pytest.fixture
def valid_key(aioclient_mock: AiohttpClientMocker) -> None:
    """A key that passes /stats with the export enabled."""
    aioclient_mock.get(f"{API}/stats", json={"success": True, "data": {}})
    aioclient_mock.get(
        f"{API}/health-export/settings",
        json={"success": True, "data": {"enabled": True, "types": []}},
    )


def entity_id_for(
    entity_registry: er.EntityRegistry, entry: MockConfigEntry, platform: str, key: str
) -> str:
    """Return the entity ID of an entity of this integration by its key."""
    entity_id = entity_registry.async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{key}"
    )
    assert entity_id is not None, key
    return entity_id


def mock_export_api(
    aioclient_mock: AiohttpClientMocker,
    today: date = date(2026, 10, 10),
    *,
    metrics: dict[str, Any] | None = None,
    sleep: dict[str, Any] | None = None,
    workouts: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> None:
    """Mock all export endpoints for the given local day."""
    aioclient_mock.get(
        f"{API}/health-export/metrics",
        json=metrics or load_fixture("export_metrics.json"),
        headers=headers,
    )
    aioclient_mock.get(
        f"{API}/health-export/settings", json=load_fixture("export_settings.json")
    )
    aioclient_mock.get(
        f"{API}/health-export/daily",
        params={
            "metric": "sleep_analysis",
            "from": (today - timedelta(days=1)).isoformat(),
            "to": today.isoformat(),
        },
        json=sleep or load_fixture("export_daily_sleep.json"),
    )
    aioclient_mock.get(
        f"{API}/health-export/daily",
        params={
            "metric": "workouts",
            "from": (today - timedelta(days=6)).isoformat(),
            "to": today.isoformat(),
        },
        json=workouts or load_fixture("export_daily_workouts.json"),
    )


def calls_to(
    aioclient_mock: AiohttpClientMocker, path: str, metric: str | None = None
) -> int:
    """Count requests to an API path (optionally for one export metric)."""
    return sum(
        1
        for call in aioclient_mock.mock_calls
        if call[1].path == f"/v1{path}"
        and (metric is None or call[1].query.get("metric") == metric)
    )
