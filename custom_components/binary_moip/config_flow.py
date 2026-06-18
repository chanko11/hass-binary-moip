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
from homeassistant.util import slugify

from .api import BinaryMoIPAuthError, BinaryMoIPClient, BinaryMoIPConnectionError
from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_MUSIC_MEDIA,
    CONF_MUSIC_PLAYER,
    CONF_MUSIC_TYPE,
    CONF_PINK_MEDIA,
    CONF_PINK_PLAYER,
    CONF_PINK_TYPE,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DEFAULT_MASTER_POSITIONS,
    DEFAULT_PORT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    OPT_BACKING,
    OPT_CALIBRATION,
    OPT_ENABLED,
    OPT_LABEL,
    OPT_SOURCES,
    OPT_SPACES,
    OPT_ZONES,
    SPACE_TERM,
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
        """Top-level menu: choose what to configure."""
        return self.async_show_menu(
            step_id="init", menu_options=["zones", "sources", "spaces", "calibration"]
        )

    async def async_step_calibration(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the pink-noise reference (a media item + its player) for the
        calibration tool. Optional — calibration also works with any source.
        """
        current = self.config_entry.options.get(OPT_CALIBRATION, {})
        if user_input is not None:
            calibration = {}

            def _capture(field, player_key, media_key, type_key):
                picked = user_input.get(field)
                if picked and picked.get("media_content_id"):
                    calibration[player_key] = picked.get("entity_id")
                    calibration[media_key] = picked.get("media_content_id")
                    calibration[type_key] = picked.get("media_content_type")
                elif current.get(media_key):  # untouched picker -> keep existing
                    calibration[player_key] = current.get(player_key)
                    calibration[media_key] = current.get(media_key)
                    calibration[type_key] = current.get(type_key)

            _capture("pink", CONF_PINK_PLAYER, CONF_PINK_MEDIA, CONF_PINK_TYPE)
            _capture("music", CONF_MUSIC_PLAYER, CONF_MUSIC_MEDIA, CONF_MUSIC_TYPE)
            options = {**self.config_entry.options, OPT_CALIBRATION: calibration}
            return self.async_create_entry(title="", data=options)

        def _suggest(media_key, player_key, type_key):
            if not current.get(media_key):
                return None
            return {
                "entity_id": current.get(player_key),
                "media_content_id": current.get(media_key),
                "media_content_type": current.get(type_key),
            }

        schema = vol.Schema(
            {
                vol.Optional(
                    "pink",
                    description={"suggested_value": _suggest(CONF_PINK_MEDIA, CONF_PINK_PLAYER, CONF_PINK_TYPE)},
                ): selector.MediaSelector(),
                vol.Optional(
                    "music",
                    description={"suggested_value": _suggest(CONF_MUSIC_MEDIA, CONF_MUSIC_PLAYER, CONF_MUSIC_TYPE)},
                ): selector.MediaSelector(),
            }
        )
        return self.async_show_form(step_id="calibration", data_schema=schema)

    # --- Listening Spaces ----------------------------------------------------

    _NEW = "__new__"
    _DONE = "__done__"

    def _zone_display_items(self) -> list[tuple[int, str]]:
        """(group_id, display name) for all zones, applying label overrides."""
        coordinator = self.config_entry.runtime_data
        overrides = self.config_entry.options.get(OPT_ZONES, {})
        items = [
            (z.group_id, overrides.get(str(z.group_id), {}).get(OPT_LABEL) or z.name)
            for z in coordinator.data.zones.values()
        ]
        return sorted(items, key=lambda it: it[1].lower())

    def _load_spaces_working_copy(self) -> None:
        if getattr(self, "_spaces", None) is None:
            self._spaces: dict[str, dict] = {
                s["id"]: {
                    "id": s["id"],
                    "name": s.get("name", s["id"]),
                    "zones": [int(z) for z in s.get("zones", [])],
                    "label": s.get("label") or None,
                    "zones_add": [int(z) for z in s.get("zones_add", [])],
                    "zones_exclude": [int(z) for z in s.get("zones_exclude", [])],
                    "master_positions": s.get("master_positions")
                    or dict(DEFAULT_MASTER_POSITIONS),
                }
                for s in self.config_entry.options.get(OPT_SPACES, [])
            }

    async def async_step_spaces(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a Listening Space to edit, add a new one, or save & finish."""
        self._load_spaces_working_copy()

        if user_input is not None:
            choice = user_input["space"]
            if choice == self._DONE:
                options = {
                    **self.config_entry.options,
                    OPT_SPACES: list(self._spaces.values()),
                }
                return self.async_create_entry(title="", data=options)
            if choice == self._NEW:
                return await self.async_step_space_new()
            self._editing = choice
            return await self.async_step_space_edit()

        options = [{"value": sid, "label": s["name"]} for sid, s in self._spaces.items()]
        options.append({"value": self._NEW, "label": f"➕ Add a {SPACE_TERM}"})
        options.append({"value": self._DONE, "label": "✓ Save & finish"})
        # Soft notice: zones not in any space (informational, not a block).
        assigned = {g for s in self._spaces.values() for g in s["zones"]}
        unassigned = [n for g, n in self._zone_display_items() if g not in assigned]
        schema = vol.Schema(
            {
                vol.Required("space"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options, mode=selector.SelectSelectorMode.LIST
                    )
                )
            }
        )
        return self.async_show_form(
            step_id="spaces",
            data_schema=schema,
            description_placeholders={
                "unassigned": ", ".join(unassigned) if unassigned else "none"
            },
        )

    async def async_step_space_new(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Name a new Listening Space, then edit its zones."""
        self._load_spaces_working_copy()
        if user_input is not None:
            name = user_input["name"].strip()
            base = slugify(name) or "space"
            sid = base
            i = 2
            while sid in self._spaces:
                sid = f"{base}_{i}"
                i += 1
            self._spaces[sid] = {
                "id": sid,
                "name": name,
                "zones": [],
                "label": None,
                "zones_add": [],
                "zones_exclude": [],
                "master_positions": dict(DEFAULT_MASTER_POSITIONS),
            }
            self._editing = sid
            return await self.async_step_space_edit()
        return self.async_show_form(
            step_id="space_new", data_schema=vol.Schema({vol.Required("name"): str})
        )

    async def async_step_space_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Rename / set member zones / delete a Listening Space."""
        space = self._spaces[self._editing]
        if user_input is not None:
            if user_input.get("delete"):
                self._spaces.pop(self._editing, None)
            else:
                space["name"] = (user_input.get("name") or space["name"]).strip()
                space["label"] = user_input.get("label") or None
                zones = [int(z) for z in user_input.get("zones", [])]
                # A label drives membership (label -> areas -> zones); the explicit
                # list is only used when there's no label. (add/exclude overrides
                # are a future advanced step.)
                space["zones"] = [] if space["label"] else zones
            return await self.async_step_spaces()

        zone_options = [
            {"value": str(gid), "label": name} for gid, name in self._zone_display_items()
        ]
        # A label drives membership; the explicit list applies only when no label.
        zones_default = [] if space.get("label") else [str(z) for z in space["zones"]]
        label_field = (
            vol.Optional("label", description={"suggested_value": space.get("label")})
            if space.get("label")
            else vol.Optional("label")
        )
        schema = vol.Schema(
            {
                vol.Required("name", default=space["name"]): str,
                label_field: selector.LabelSelector(),
                vol.Optional("zones", default=zones_default): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=zone_options,
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
                vol.Optional("delete", default=False): bool,
            }
        )
        return self.async_show_form(
            step_id="space_edit",
            data_schema=schema,
            description_placeholders={"space": space["name"]},
        )

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
