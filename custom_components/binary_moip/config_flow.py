"""Config flow for the Binary MoIP integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.components.media_player import DOMAIN as MEDIA_PLAYER_DOMAIN
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
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
    OPT_BACKING,
    OPT_ENABLED,
    OPT_LABEL,
    OPT_SOURCES,
    OPT_ZONES,
)
from .media_player import _build_source_maps

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


_ENABLED_SUFFIX = ""  # enabled checkbox keyed by the item's display name
_LABEL_SUFFIX = " — custom name"
_BACKING_SUFFIX = " — backing media_player"


def _unique_displays(items: list[tuple[int, str]]) -> dict[int, str]:
    """Map each item id to a unique display string (append [id] on collision)."""
    counts: dict[str, int] = {}
    for _, name in items:
        counts[name] = counts.get(name, 0) + 1
    displays: dict[int, str] = {}
    for iid, name in items:
        displays[iid] = name if counts[name] == 1 else f"{name} [{iid}]"
    return displays


def _build_options_schema(
    items: list[tuple[int, str]], existing: dict, *, include_backing: bool = False
) -> tuple[vol.Schema, dict[str, int], dict[str, int], dict[str, int]]:
    """Build a per-item enable+label (+optional backing) schema.

    Returns (schema, enabled_key->id, label_key->id, backing_key->id). Each item
    gets an enabled boolean (default True) keyed by its display name and an
    optional free-text label keyed by "<display> — custom name". When
    ``include_backing`` is set (sources only), each item also gets an optional
    media_player entity picker keyed by "<display> — backing media_player".
    ``backing_keys`` is empty when ``include_backing`` is False.
    """
    displays = _unique_displays(items)
    schema: dict = {}
    enabled_keys: dict[str, int] = {}
    label_keys: dict[str, int] = {}
    backing_keys: dict[str, int] = {}
    for iid, _ in items:
        disp = displays[iid]
        cur = existing.get(str(iid), {})
        ekey = f"{disp}{_ENABLED_SUFFIX}"
        lkey = f"{disp}{_LABEL_SUFFIX}"
        schema[vol.Optional(ekey, default=cur.get(OPT_ENABLED, True))] = bool
        schema[vol.Optional(lkey, default=cur.get(OPT_LABEL, ""))] = str
        enabled_keys[ekey] = iid
        label_keys[lkey] = iid
        if include_backing:
            bkey = f"{disp}{_BACKING_SUFFIX}"
            # Optional entity picker; suggested_value pre-fills the current
            # mapping without forcing a default (so it can be cleared).
            schema[
                vol.Optional(bkey, description={"suggested_value": cur.get(OPT_BACKING)})
            ] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain=MEDIA_PLAYER_DOMAIN)
            )
            backing_keys[bkey] = iid
    return vol.Schema(schema), enabled_keys, label_keys, backing_keys


def _parse_options(
    user_input: dict,
    enabled_keys: dict[str, int],
    label_keys: dict[str, int],
    backing_keys: dict[str, int] | None = None,
) -> dict:
    """Fold submitted form values into the compact options map for a category.

    Only stores non-default values: ``enabled`` is omitted when True (the
    default), ``label``/``backing_entity`` when blank — so an untouched system
    yields ``{}``.
    """
    result: dict[str, dict] = {}
    for key, iid in enabled_keys.items():
        if user_input.get(key, True) is False:
            result.setdefault(str(iid), {})[OPT_ENABLED] = False
    for key, iid in label_keys.items():
        label = (user_input.get(key) or "").strip()
        if label:
            result.setdefault(str(iid), {})[OPT_LABEL] = label
    for key, iid in (backing_keys or {}).items():
        backing = (user_input.get(key) or "").strip()
        if backing:
            result.setdefault(str(iid), {})[OPT_BACKING] = backing
    return result


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
        return self.async_show_menu(step_id="init", menu_options=["zones", "sources"])

    async def async_step_zones(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enable/disable and label zones (group_rx)."""
        coordinator = self.config_entry.runtime_data
        items = sorted(
            ((z.group_id, z.name) for z in coordinator.data.zones.values()),
            key=lambda it: it[1].lower(),
        )
        existing = self.config_entry.options.get(OPT_ZONES, {})
        schema, enabled_keys, label_keys, _ = _build_options_schema(items, existing)

        if user_input is not None:
            new_map = _parse_options(user_input, enabled_keys, label_keys)
            options = {**self.config_entry.options, OPT_ZONES: new_map}
            return self.async_create_entry(title="", data=options)

        return self.async_show_form(step_id="zones", data_schema=schema)

    async def async_step_sources(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enable/disable, label, and optionally back each source (group_tx).

        Source rows use synthesized, unique display names (parent unit +
        hardware label + input type) since controller source names are often
        non-unique defaults. Each row also offers an optional backing
        media_player whose transport + now-playing the source proxies.
        """
        coordinator = self.config_entry.runtime_data
        labels, _ = _build_source_maps(coordinator.data, {})  # synthesized names
        items = sorted(
            ((sid, labels[sid]) for sid in coordinator.data.sources),
            key=lambda it: it[1].lower(),
        )
        existing = self.config_entry.options.get(OPT_SOURCES, {})
        schema, enabled_keys, label_keys, backing_keys = _build_options_schema(
            items, existing, include_backing=True
        )

        if user_input is not None:
            new_map = _parse_options(user_input, enabled_keys, label_keys, backing_keys)
            options = {**self.config_entry.options, OPT_SOURCES: new_map}
            return self.async_create_entry(title="", data=options)

        return self.async_show_form(step_id="sources", data_schema=schema)
