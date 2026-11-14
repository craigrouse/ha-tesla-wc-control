"""Config flow for Tesla Wall Connector Control. No fields: it uses an existing Tesla Fleet or Teslemetry login."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN


class TeslaWcControlConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tesla Wall Connector Control."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial (and only) step."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(title="Tesla Wall Connector Control", data={})

        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))
