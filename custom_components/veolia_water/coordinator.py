"""DataUpdateCoordinator — owns the cycle and the persistent state store."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_CONTRACT_NUMBER,
    CONF_PORTAL_URL,
    CONF_SCAN_INTERVAL_HOURS,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
    STATS_IMPORT_VERSION,
    STATS_SOURCE,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .models import Snapshot, FlowSummary
from .parser import (
    ParseError,
    parse_caudales_response,
    parse_daily_response,
    parse_inicio,
    parse_monthly_response,
    summarize_flow,
)
from .portal import DEFAULT_BASE_URL
from .stats import import_daily_series
from .veolia_client import (
    CDNBlockedError,
    LoginError,
    SessionExpiredError,
    VeoliaClient,
    VeoliaError,
)

_LOGGER = logging.getLogger(__name__)


class VeoliaCoordinator(DataUpdateCoordinator[Snapshot]):
    """Runs one cycle (login + fetch + parse + derive) on a configurable cadence."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        opts = {**entry.data, **entry.options}
        interval_h = int(opts.get(CONF_SCAN_INTERVAL_HOURS, DEFAULT_SCAN_INTERVAL_HOURS))
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(hours=max(1, interval_h)),
        )
        self.hass = hass
        self.entry = entry
        self._username: str = opts[CONF_USERNAME]
        self._password: str = opts[CONF_PASSWORD]
        self._base_url: str = (opts.get(CONF_PORTAL_URL) or DEFAULT_BASE_URL).rstrip("/")
        self._contract_filter: Optional[str] = (opts.get(CONF_CONTRACT_NUMBER) or "").strip() or None
        self._store: Store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}_{entry.entry_id}")
        self._persisted: dict[str, Any] = {}

    async def async_init_store(self) -> None:
        self._persisted = (await self._store.async_load()) or {}

    async def _async_update_data(self) -> Snapshot:
        try:
            snapshot = await self._fetch_cycle()
        except LoginError as e:
            raise UpdateFailed(f"login: {e}") from e
        except CDNBlockedError as e:
            raise UpdateFailed(f"cdn blocked: {e}") from e
        except (ParseError, VeoliaError) as e:
            raise UpdateFailed(f"portal: {e}") from e

        await self._maybe_import_statistics(snapshot)
        return snapshot

    async def _fetch_cycle(self) -> Snapshot:
        async with VeoliaClient(self._username, self._password, base_url=self._base_url) as client:
            await client.login()
            html = await client.fetch_inicio()
            contract, reading, invoice, history = parse_inicio(html)

            if self._contract_filter and contract.contract_number != self._contract_filter:
                raise VeoliaError(
                    f"Configured contract_number={self._contract_filter} but portal shows "
                    f"{contract.contract_number}. Switch contracts on the portal first."
                )

            caudales, daily, monthly, flow = await self._fetch_smart_meter_data(client, contract.smart_metering)

            if invoice.period_end is not None:
                invoice.current_period_end_estimate = invoice.period_end + timedelta(days=91)
                invoice.next_invoice_date_estimate = (
                    invoice.current_period_end_estimate + timedelta(days=5)
                )

            _apply_daily_derivations(reading, daily)

            return Snapshot(
                contract=contract,
                reading=reading,
                invoice=invoice,
                history=history,
                flow=flow,
                caudales=caudales,
                daily=daily,
                monthly=monthly,
            )

    async def _fetch_smart_meter_data(self, client: VeoliaClient, is_smart: bool):
        caudales: list = []
        daily: list = []
        monthly: list = []
        flow = FlowSummary()
        if not is_smart:
            return caudales, daily, monthly, flow

        today = datetime.now(timezone.utc).date()
        try:
            consumos_html = await client.fetch_consumos_page()
        except SessionExpiredError:
            _LOGGER.warning("Session expired during smart-meter fetch — skipping this cycle.")
            return caudales, daily, monthly, flow

        p_auth = client.extract_auth_token(consumos_html)
        if not p_auth:
            _LOGGER.warning("No p_auth on consumos page; skipping smart-meter data.")
            return caudales, daily, monthly, flow

        async def _safe(label: str, coro):
            try:
                return await coro
            except (VeoliaError, ValueError) as e:
                _LOGGER.warning("%s fetch failed: %s", label, e)
                return None

        caudales_payload = await _safe(
            "caudales",
            client.fetch_caudales(p_auth, today - timedelta(days=100), today),
        )
        if caudales_payload:
            caudales = parse_caudales_response(caudales_payload)
            flow = summarize_flow(caudales)

        daily_payload = await _safe(
            "daily consumption",
            client.fetch_buscar_consumos(p_auth, today - timedelta(days=100), today, tipo="diaria"),
        )
        if daily_payload:
            daily = parse_daily_response(daily_payload)

        monthly_payload = await _safe(
            "monthly consumption",
            client.fetch_buscar_consumos(
                p_auth, today - timedelta(days=730), today, tipo="mensual", fin=999,
            ),
        )
        if monthly_payload:
            monthly = parse_monthly_response(monthly_payload)

        return caudales, daily, monthly, flow

    async def _maybe_import_statistics(self, snap: Snapshot) -> None:
        """One-time backfill of daily / monthly history into HA's recorder."""
        cnum = snap.contract.contract_number
        contracts_state = self._persisted.setdefault("contracts", {})
        cstate = contracts_state.setdefault(cnum, {})
        if cstate.get("stats_imported_version", 0) >= STATS_IMPORT_VERSION:
            return
        if not (snap.caudales or snap.daily or snap.monthly):
            return

        slug = _slug(cnum)
        any_ok = False
        if snap.caudales:
            any_ok |= import_daily_series(
                self.hass,
                statistic_id=f"{STATS_SOURCE}:flow_qmax_{slug}",
                name=f"Veolia {cnum} daily peak flow",
                unit_of_measurement="m³/h",
                daily_values=[(c.fecha, c.q_max_m3h) for c in snap.caudales],
            )
        if snap.daily:
            any_ok |= import_daily_series(
                self.hass,
                statistic_id=f"{STATS_SOURCE}:daily_consumption_{slug}",
                name=f"Veolia {cnum} daily consumption",
                unit_of_measurement="m³",
                daily_values=[(d.fecha, d.consumo_m3) for d in snap.daily],
            )
            any_ok |= import_daily_series(
                self.hass,
                statistic_id=f"{STATS_SOURCE}:meter_index_{slug}",
                name=f"Veolia {cnum} meter index",
                unit_of_measurement="m³",
                daily_values=[(d.fecha, d.lectura_m3) for d in snap.daily],
            )
        if snap.monthly:
            any_ok |= import_daily_series(
                self.hass,
                statistic_id=f"{STATS_SOURCE}:monthly_consumption_{slug}",
                name=f"Veolia {cnum} monthly consumption",
                unit_of_measurement="m³",
                daily_values=[(date(m.year, m.month, 1), m.consumo_m3) for m in snap.monthly],
            )
        if any_ok:
            cstate["stats_imported_version"] = STATS_IMPORT_VERSION
            await self._store.async_save(self._persisted)


def _apply_daily_derivations(reading, daily: list) -> None:
    """Refresh meter index + derive today / 7-day / month-to-date metrics."""
    if not daily:
        return
    sorted_daily = sorted(daily, key=lambda x: x.fecha, reverse=True)
    latest = sorted_daily[0]
    if latest.lectura_m3 is not None:
        reading.meter_index_m3 = latest.lectura_m3
        reading.last_reading_date = latest.fecha
    if latest.consumo_m3 is not None:
        reading.latest_daily_consumption_m3 = latest.consumo_m3
        reading.latest_daily_consumption_l = int(round(latest.consumo_m3 * 1000))

    last7 = [d.consumo_m3 for d in sorted_daily[:7] if d.consumo_m3 is not None]
    if last7:
        reading.rolling_7d_avg_l = int(round(sum(last7) / len(last7) * 1000))

    today = datetime.now(timezone.utc).date()
    mtd = [
        d.consumo_m3 for d in daily
        if d.consumo_m3 is not None
        and d.fecha.year == today.year
        and d.fecha.month == today.month
    ]
    if mtd:
        reading.month_to_date_m3 = round(sum(mtd), 3)


def _slug(contract_number: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in contract_number).lower() or "unknown"
