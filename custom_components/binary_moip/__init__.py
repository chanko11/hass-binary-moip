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
from .coordinator import BinaryMoIPConfigEntry, BinaryMoIPDataUpdateCoordinator


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

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BinaryMoIPConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
