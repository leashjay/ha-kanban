"""Config flow for Hakanban (single instance, nothing to configure) and its options."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback

from .const import CONF_PANEL_ICON, CONF_PANEL_TITLE, DOMAIN, PANEL_TITLE
from .options import clean_panel_options, panel_settings


class HakanbanConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Hakanban."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> HakanbanOptionsFlow:
        """Offer the name/icon options."""
        return HakanbanOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Single-click setup — there is nothing to configure."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title=PANEL_TITLE, data={})
        return self.async_show_form(step_id="user")


class HakanbanOptionsFlow(OptionsFlowWithReload):
    """Rename the sidebar entry and change its icon (the entry reloads to apply it)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            options, errors = clean_panel_options(user_input)
            if not errors:
                return self.async_create_entry(data=options)
        title, icon = panel_settings(self.config_entry.options)
        shown = user_input or {}
        schema = vol.Schema(
            {
                vol.Optional(CONF_PANEL_TITLE, default=shown.get(CONF_PANEL_TITLE, title)): str,
                vol.Optional(CONF_PANEL_ICON, default=shown.get(CONF_PANEL_ICON, icon)): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
