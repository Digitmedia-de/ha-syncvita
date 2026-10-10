"""Config flow for Syncvita (placeholder, replaced by the full flow)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigFlow

from .const import DOMAIN


class SyncvitaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Syncvita."""

    VERSION = 1
