"""Config flow for the Binary MoIP integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

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
            await self.async_set_unique_id(user_input[CONF_HOST])
            self._abort_if_unique_id_configured()
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
        """Authenticate against the controller to validate host + credentials.

        Raises BinaryMoIPConnectionError / BinaryMoIPAuthError on failure, which
        the caller maps to form errors.
        """
        session = async_get_clientsession(
            self.hass, verify_ssl=user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
        )
        client = BinaryMoIPClient(
            session,
            user_input[CONF_HOST],
            port=user_input.get(CONF_PORT, DEFAULT_PORT),
            username=user_input[CONF_USERNAME],
            password=user_input[CONF_PASSWORD],
            verify_ssl=user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
        )
        await client.authenticate()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> BinaryMoIPOptionsFlow:
        """Return the options flow handler."""
        return BinaryMoIPOptionsFlow()


class BinaryMoIPOptionsFlow(OptionsFlow):
    """Options flow: enable/disable and label each discovered zone and source.

    The integration discovers ALL zones (group_rx) and sources (group_tx) from
    the controller. This flow lets the user pick which surface in normal HA
    pickers and give each a friendly HA-side label. Selections persist in
    ``config_entry.options`` under OPT_ZONES / OPT_SOURCES. See
    docs/naming-and-discovery.md.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Top-level menu: choose to configure zones or sources."""
        return self.async_show_menu(
            step_id="init", menu_options=["zones", "sources"]
        )

    async def async_step_zones(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enable/disable and label zones (group_rx).

        Schema is built dynamically from the coordinator's discovered zones.
        """
        raise NotImplementedError

    async def async_step_sources(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enable/disable and label sources (group_tx).

        Source rows show synthesized disambiguating info (parent unit +
        hardware label + input type) since controller source names are often
        non-unique defaults.
        """
        raise NotImplementedError
