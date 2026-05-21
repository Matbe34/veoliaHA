"""Config + options flow for the Veolia Water integration."""
from __future__ import annotations

from typing import Any, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_CONTRACT_NUMBER,
    CONF_PORTAL_URL,
    CONF_SCAN_INTERVAL_HOURS,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
)
from .portal import DEFAULT_BASE_URL
from .veolia_client import (
    CDNBlockedError,
    LoginError,
    VeoliaClient,
    VeoliaError,
)

USER_SCHEMA = vol.Schema({
    vol.Required(CONF_USERNAME): str,
    vol.Required(CONF_PASSWORD): str,
    vol.Optional(CONF_PORTAL_URL, default=DEFAULT_BASE_URL): str,
    vol.Optional(CONF_CONTRACT_NUMBER, default=""): str,
})


class VeoliaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial credentials / portal-url setup."""

    VERSION = 1

    async def async_step_user(self, user_input: Optional[dict[str, Any]] = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            error = await _validate_credentials(
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                user_input.get(CONF_PORTAL_URL) or DEFAULT_BASE_URL,
            )
            if error is None:
                await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Veolia Water — {user_input[CONF_USERNAME]}",
                    data=user_input,
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="user", data_schema=USER_SCHEMA, errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        entry: config_entries.ConfigEntry,
    ) -> "VeoliaOptionsFlow":
        return VeoliaOptionsFlow(entry)


class VeoliaOptionsFlow(config_entries.OptionsFlow):
    """Post-install: change poll interval and contract filter without re-entering credentials."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry

    async def async_step_init(self, user_input: Optional[dict[str, Any]] = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {**self._entry.data, **self._entry.options}
        schema = vol.Schema({
            vol.Optional(
                CONF_SCAN_INTERVAL_HOURS,
                default=current.get(CONF_SCAN_INTERVAL_HOURS, DEFAULT_SCAN_INTERVAL_HOURS),
            ): vol.All(int, vol.Range(min=1, max=24)),
            vol.Optional(
                CONF_CONTRACT_NUMBER,
                default=current.get(CONF_CONTRACT_NUMBER, ""),
            ): str,
        })
        return self.async_show_form(step_id="init", data_schema=schema)


async def _validate_credentials(username: str, password: str, base_url: str) -> Optional[str]:
    """Return None on success, otherwise an error key for the form."""
    try:
        async with VeoliaClient(username, password, base_url=base_url) as client:
            await client.login()
    except LoginError:
        return "invalid_auth"
    except CDNBlockedError:
        return "cdn_blocked"
    except VeoliaError:
        return "cannot_connect"
    except Exception:  # noqa: BLE001 — surface unexpected issues to the user clearly
        return "unknown"
    return None
