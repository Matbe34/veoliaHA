"""The "Get data now" button."""
import asyncio

import pytest

pytest.importorskip("homeassistant.components.button")

from custom_components.veolia_water.button import VeoliaRefreshButton  # noqa: E402


class _Coordinator:
    def __init__(self):
        self.refreshes = 0
        self.data = None
        self.last_update_success = False

    async def async_request_refresh(self):
        self.refreshes += 1


def test_pressing_asks_the_coordinator_for_a_refresh_even_after_a_failed_poll():
    coordinator = _Coordinator()
    button = VeoliaRefreshButton(coordinator, "123")
    assert button.available  # last poll failed, still pressable
    assert button.unique_id == "123_refresh"
    assert button.device_info["identifiers"] == {("veolia_water", "123")}
    asyncio.run(button.async_press())
    assert coordinator.refreshes == 1
