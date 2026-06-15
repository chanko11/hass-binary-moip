"""The Binary MoIP integration."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BinaryMoIPClient
from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DEFAULT_PORT,
    DEFAULT_VERIFY_SSL,
    PLATFORMS,
)
from .const import DOMAIN
from .coordinator import BinaryMoIPConfigEntry, BinaryMoIPDataUpdateCoordinator
from .services import async_register_services, async_unregister_services
from .spaces import SpaceManager


async def async_setup_entry(hass: HomeAssistant, entry: BinaryMoIPConfigEntry) -> bool:
    """Set up Binary MoIP from a config entry."""
    session = async_get_clientsession(hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL))

    client = BinaryMoIPClient(
        session,
        entry.data[CONF_HOST],
        port=entry.data.get(CONF_PORT, DEFAULT_PORT),
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
    )

    coordinator = BinaryMoIPDataUpdateCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    # Listening Spaces: load definitions + register playback services.
    manager = SpaceManager(hass, coordinator)
    await manager.async_load()
    coordinator.space_manager = manager
    async_register_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Real-time updates via the change-event websocket (polling is the fallback).
    coordinator.start_websocket()
    # Reload when options (zone/source enable + labels) change.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: BinaryMoIPConfigEntry
) -> None:
    """Reload the entry so option changes take effect."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: BinaryMoIPConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    # Drop the Space services once the last binary_moip entry is gone.
    if unloaded and len(hass.config_entries.async_loaded_entries(DOMAIN)) <= 1:
        async_unregister_services(hass)
    return unloaded
