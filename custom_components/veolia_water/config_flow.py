"""Config + options flow for the Veolia Water integration."""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_CONTRACTS,
    CONF_PORTAL_SITE,
    CONF_PORTAL_URL,
    CONF_SCAN_INTERVAL_HOURS,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
)
from .cycle import configured_contracts
from .models import Contract
from .portal import DEFAULT_BASE_URL, PortalProfile, apply_overrides, profile_for_url
from .veolia_client import (
    CDNBlockedError,
    LoginError,
    UnsupportedPortalError,
    VeoliaClient,
    VeoliaError,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema({
    vol.Required(CONF_USERNAME): str,
    vol.Required(CONF_PASSWORD): str,
    vol.Optional(CONF_PORTAL_URL, default=DEFAULT_BASE_URL): str,
})


class VeoliaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Credentials first, then which contracts to add if the account has several."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._choices: list[Contract] = []

    async def async_step_user(self, user_input: Optional[dict[str, Any]] = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
            self._abort_if_unique_id_configured()
            base_url = user_input.get(CONF_PORTAL_URL) or DEFAULT_BASE_URL
            error, contracts = await _login_and_list(
                user_input[CONF_USERNAME], user_input[CONF_PASSWORD], base_url,
            )
            if error is None:
                self._data = {**user_input, CONF_PORTAL_URL: base_url}
                if contracts and len(contracts) > 1:
                    self._choices = contracts
                    return await self.async_step_contracts()
                # One contract, or a portal that wouldn't list them (empty list:
                # read whichever contract the portal opens on).
                self._data[CONF_CONTRACTS] = [c.contract_number for c in contracts or []]
                return self._create_entry()
            errors["base"] = error

        return self.async_show_form(
            step_id="user", data_schema=USER_SCHEMA, errors=errors,
        )

    async def async_step_contracts(
        self, user_input: Optional[dict[str, Any]] = None,
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            chosen = user_input.get(CONF_CONTRACTS) or []
            if chosen:
                self._data[CONF_CONTRACTS] = chosen
                return self._create_entry()
            errors["base"] = "no_contracts_selected"

        everything = [c.contract_number for c in self._choices]
        schema = vol.Schema({
            vol.Required(CONF_CONTRACTS, default=everything): _contract_selector(self._choices),
        })
        return self.async_show_form(step_id="contracts", data_schema=schema, errors=errors)

    def _create_entry(self) -> FlowResult:
        return self.async_create_entry(
            title=f"Veolia Water — {self._data[CONF_USERNAME]}",
            data=self._data,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        entry: config_entries.ConfigEntry,
    ) -> "VeoliaOptionsFlow":
        return VeoliaOptionsFlow(entry)


class VeoliaOptionsFlow(config_entries.OptionsFlow):
    """Post-install: poll interval, contracts and site, without re-entering credentials."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry
        self._choices: Optional[list[Contract]] = None

    async def async_step_init(self, user_input: Optional[dict[str, Any]] = None) -> FlowResult:
        current = {**self._entry.data, **self._entry.options}
        selected = configured_contracts(current)
        errors: dict[str, str] = {}

        if self._choices is None:
            # Fetched live, so a contract added to the account shows up here.
            self._choices = await _list_for_entry(current)
            if self._choices is None:
                errors["base"] = "cannot_list_contracts"
        choices = self._choices or []
        listed = {c.contract_number for c in choices}
        show_picker = len(listed | set(selected)) > 1

        if user_input is not None:
            if not show_picker:
                # Nothing to choose from (or no list): keep what's configured.
                user_input[CONF_CONTRACTS] = selected
                return self.async_create_entry(title="", data=user_input)
            if user_input.get(CONF_CONTRACTS):
                return self.async_create_entry(title="", data=user_input)
            errors = {"base": "no_contracts_selected"}

        fields: dict[Any, Any] = {
            vol.Optional(
                CONF_SCAN_INTERVAL_HOURS,
                default=current.get(CONF_SCAN_INTERVAL_HOURS, DEFAULT_SCAN_INTERVAL_HOURS),
            ): vol.All(int, vol.Range(min=1, max=24)),
        }
        if show_picker:
            fields[vol.Required(CONF_CONTRACTS, default=selected)] = _contract_selector(
                choices, extra=selected,
            )
        fields[vol.Optional(
            CONF_PORTAL_SITE,
            default=current.get(CONF_PORTAL_SITE, ""),
        )] = str
        return self.async_show_form(
            step_id="init", data_schema=vol.Schema(fields), errors=errors,
        )


def _contract_selector(contracts: list[Contract], extra: Sequence[str] = ()) -> SelectSelector:
    """Tick list of contracts. `extra` keeps configured numbers the portal no longer lists."""
    options = [SelectOptionDict(value=c.contract_number, label=c.label) for c in contracts]
    listed = {c.contract_number for c in contracts}
    options += [SelectOptionDict(value=n, label=n) for n in extra if n not in listed]
    return SelectSelector(SelectSelectorConfig(
        options=options, multiple=True, mode=SelectSelectorMode.LIST,
    ))


async def _login_and_list(
    username: str,
    password: str,
    base_url: str,
    profile: Optional[PortalProfile] = None,
) -> tuple[Optional[str], Optional[list[Contract]]]:
    """Log in and list the account's contracts.

    Returns (error key for the form, contracts). Contracts is None when the
    login worked but the portal wouldn't list them: that must not block setup.
    """
    try:
        async with VeoliaClient(
            username, password, base_url=base_url, profile=profile,
        ) as client:
            await client.login()
            try:
                return None, await client.list_contracts()
            except VeoliaError as e:
                _LOGGER.warning("Could not list the account's contracts: %s", e)
                return None, None
    except LoginError:
        return "invalid_auth", None
    except UnsupportedPortalError:
        return "unsupported_portal", None
    except CDNBlockedError:
        return "cdn_blocked", None
    except VeoliaError:
        return "cannot_connect", None
    except Exception:  # noqa: BLE001 — surface unexpected issues to the user clearly
        _LOGGER.exception("Unexpected error during login")
        return "unknown", None


async def _list_for_entry(current: dict[str, Any]) -> Optional[list[Contract]]:
    """The account's contracts, logging in with the entry's stored settings."""
    base_url = (current.get(CONF_PORTAL_URL) or DEFAULT_BASE_URL).rstrip("/")
    profile = apply_overrides(profile_for_url(base_url), {"site": current.get(CONF_PORTAL_SITE)})
    error, contracts = await _login_and_list(
        current[CONF_USERNAME], current[CONF_PASSWORD], base_url, profile,
    )
    if error is not None:
        _LOGGER.warning("Could not log in to list contracts: %s", error)
    return contracts
