"""Veolia Water integration.

HA-specific imports are deferred to the lifecycle hooks so that the pure
submodules (`parser`, `models`, `portal`, `veolia_client`) remain importable
outside HA — useful for unit-testing.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .const import DOMAIN, PLATFORMS

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    """Set up a Veolia Water config entry."""
    from .coordinator import VeoliaCoordinator

    coordinator = VeoliaCoordinator(hass, entry)
    await coordinator.async_init_store()
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options))
    return True


async def async_unload_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id, None)
        return True
    return False


async def _async_reload_on_options(hass: "HomeAssistant", entry: "ConfigEntry") -> None:
    await hass.config_entries.async_reload(entry.entry_id)
