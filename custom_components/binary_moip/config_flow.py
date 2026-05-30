"""Config flow for the Binary MoIP integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .api import BinaryMoIPAuthError, BinaryMoIPClient, BinaryMoIPConnectionError
from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DEFAULT_PORT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
    }
)


class BinaryMoIPConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Binary MoIP."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step (manual host/credentials entry)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # TODO: validate the connection by authenticating against the
            # controller, then set a unique_id from the controller serial.
            try:
                await self._async_validate(user_input)
            except BinaryMoIPConnectionError:
                errors["base"] = "cannot_connect"
            except BinaryMoIPAuthError:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error validating MoIP connection")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=user_input[CONF_HOST], data=user_input
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def _async_validate(self, user_input: dict[str, Any]) -> None:
        """Validate credentials against the controller. (Skeleton.)"""
        raise NotImplementedError
