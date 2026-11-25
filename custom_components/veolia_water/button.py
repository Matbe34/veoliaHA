"""Button that polls the portal right away instead of waiting for the next scheduled poll."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import VeoliaCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: VeoliaCoordinator = hass.data[DOMAIN][entry.entry_id]
    numbers = coordinator.contracts or list(coordinator.data or {})
    async_add_entities(VeoliaRefreshButton(coordinator, number) for number in numbers)


class VeoliaRefreshButton(CoordinatorEntity[VeoliaCoordinator], ButtonEntity):
    """Fetches fresh data for every contract of the account, like a scheduled poll."""

    _attr_has_entity_name = True
    _attr_translation_key = "refresh"
    _attr_icon = "mdi:cloud-refresh"

    def __init__(self, coordinator: VeoliaCoordinator, contract_number: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{contract_number}_refresh"
        # Same identifiers as the sensors, so it lands on the same device.
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, contract_number)})

    @property
    def available(self) -> bool:
        # A failed poll must not stop you pressing it again.
        return True

    async def async_press(self) -> None:
        # Debounced, so a double press doesn't log in to the portal twice.
        await self.coordinator.async_request_refresh()
