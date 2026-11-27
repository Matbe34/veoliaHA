"""One poll of the portal: log in once, then read each configured contract.

The portal shows one contract at a time, so for every contract after the first
the cycle switches to it (`select_contract`) and checks the switch took before
reading anything. Kept free of Home Assistant imports so it can be unit-tested.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from .const import (
    BACKFILL_DAYS,
    CONF_CONTRACT_NUMBER,
    CONF_CONTRACTS,
    DAILY_REFRESH_DAYS,
    HOURLY_REFRESH_DAYS,
    MONTHLY_WINDOW_MONTHS,
    PORTAL_TIMEZONE,
)
from .derive import apply_daily_derivations
from .models import FlowSummary, Snapshot
from .parser import (
    ParseError,
    parse_caudales_response,
    parse_daily_response,
    parse_hourly_response,
    parse_inicio,
    parse_monthly_response,
    summarize_flow,
)
from .veolia_client import (
    CDNBlockedError,
    LoginError,
    SessionExpiredError,
    VeoliaClient,
    VeoliaError,
    month_start,
)

_LOGGER = logging.getLogger(__name__)
_TZ = ZoneInfo(PORTAL_TIMEZONE)

# The history series that get a full backfill when the recorder has none of them.
SERIES = ("hourly", "daily", "monthly")
# `stored(contract_number, series)`: does the recorder already hold that series?
StoredLookup = Callable[[str, str], Awaitable[bool]]


class ContractMismatchError(VeoliaError):
    """The portal kept showing another contract after we asked to switch."""


@dataclass
class CycleResult:
    """What one poll got: a snapshot per contract read, an error per contract not."""

    snapshots: dict[str, Snapshot] = field(default_factory=dict)
    errors: dict[str, Exception] = field(default_factory=dict)


def configured_contracts(opts: Mapping[str, Any]) -> list[str]:
    """The contract numbers to read, in order. Empty means the one the portal opens on.

    Entries set up before the contract picker existed have a single
    `contract_number` string instead of a `contracts` list.
    """
    raw = opts.get(CONF_CONTRACTS)
    if raw is None:
        raw = [opts.get(CONF_CONTRACT_NUMBER) or ""]
    numbers: list[str] = []
    for value in raw:
        number = str(value or "").strip()
        if number and number not in numbers:
            numbers.append(number)
    return numbers


async def run_cycle(
    client: VeoliaClient, wanted: list[str], stored: Optional[StoredLookup] = None,
) -> CycleResult:
    """Log in and read every contract in `wanted` (or the default one if empty).

    A contract that fails is recorded in `errors` and the rest are still read.
    A failed login or a CDN block ends the cycle, since nothing else can work.

    A series `stored` says the recorder lacks is read back two years; the rest
    only get their recent window. Without `stored`, nothing is backfilled.
    """
    await client.login()
    parsed = parse_inicio(await client.fetch_inicio())
    result = CycleResult()

    if not wanted:
        snapshot = await _build_snapshot(client, parsed, stored)
        result.snapshots[snapshot.contract.contract_number] = snapshot
        return result

    # Start with the contract the portal opened on: it needs no switch.
    shown: Optional[str] = parsed[0].contract_number
    for number in sorted(wanted, key=lambda n: n != shown):
        try:
            if number != shown:
                shown = None  # unknown until the switch is confirmed
                await client.select_contract(number)
                parsed = parse_inicio(await client.fetch_inicio())
                shown = parsed[0].contract_number
                # Never report one contract's readings under another's number.
                if shown != number:
                    raise ContractMismatchError(
                        f"asked the portal for contract {number} but it showed "
                        f"contract {shown}"
                    )
            result.snapshots[number] = await _build_snapshot(client, parsed, stored)
        except (LoginError, CDNBlockedError):
            raise
        except (ParseError, VeoliaError) as e:
            _LOGGER.warning("Contract %s not read this cycle: %s", number, e)
            result.errors[number] = e
    return result


async def _build_snapshot(
    client: VeoliaClient, parsed, stored: Optional[StoredLookup] = None,
) -> Snapshot:
    """Everything for the contract the session currently shows."""
    contract, reading, invoice, history = parsed
    backfill: set[str] = set()
    if stored is not None and contract.smart_metering is not False:
        backfill = {s for s in SERIES if not await stored(contract.contract_number, s)}
    caudales, daily, monthly, flow, hourly = await _fetch_smart_meter_data(
        client, contract.smart_metering, backfill=backfill
    )

    if invoice.period_end is not None:
        invoice.current_period_end_estimate = invoice.period_end + timedelta(days=91)
        invoice.next_invoice_date_estimate = (
            invoice.current_period_end_estimate + timedelta(days=5)
        )

    # The sensors describe the recent window only, however much history came back.
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=DAILY_REFRESH_DAYS)
    apply_daily_derivations(reading, [d for d in daily if d.fecha >= cutoff])

    # After the daily derivations, which would overwrite it with the reading
    # at the start of the day: the hourly series is the freshest source.
    if hourly:
        latest = hourly[-1].lectura_m3
        if latest is not None and (
            reading.meter_index_m3 is None or latest >= reading.meter_index_m3
        ):
            reading.meter_index_m3 = latest
            reading.last_reading_date = hourly[-1].start.astimezone(_TZ).date()

    return Snapshot(
        contract=contract,
        reading=reading,
        invoice=invoice,
        history=history,
        flow=flow,
        caudales=caudales,
        daily=daily,
        monthly=monthly,
        hourly=hourly,
    )


async def _fetch_smart_meter_data(
    client: VeoliaClient, is_smart: Optional[bool], *, backfill: Collection[str] = (),
):
    caudales: list = []
    daily: list = []
    monthly: list = []
    hourly: list = []
    flow = FlowSummary()
    # Only skip when the portal said "not a smart meter". If it didn't say
    # (no `contrato` block), probe anyway: the endpoints degrade quietly.
    if is_smart is False:
        return caudales, daily, monthly, flow, hourly

    today = datetime.now(timezone.utc).date()
    try:
        consumos_html = await client.fetch_consumos_page()
    except SessionExpiredError:
        _LOGGER.warning("Session expired during smart-meter fetch; skipping this cycle.")
        return caudales, daily, monthly, flow, hourly
    except VeoliaError as e:
        _LOGGER.warning("Consumos page unavailable (%s); skipping smart-meter data.", e)
        return caudales, daily, monthly, flow, hourly

    p_auth = client.extract_auth_token(consumos_html)
    if not p_auth:
        _LOGGER.warning("No p_auth on consumos page; skipping smart-meter data.")
        return caudales, daily, monthly, flow, hourly

    async def _safe(label: str, coro):
        try:
            return await coro
        except (VeoliaError, ValueError) as e:
            _LOGGER.warning("%s fetch failed: %s", label, e)
            return None

    def _since(series: str, refresh_days: int):
        return today - timedelta(days=BACKFILL_DAYS if series in backfill else refresh_days)

    caudales_payload = await _safe(
        "caudales",
        client.fetch_caudales(p_auth, today - timedelta(days=100), today),
    )
    if caudales_payload:
        caudales = parse_caudales_response(caudales_payload)
        flow = summarize_flow(caudales)

    daily_payload = await _safe(
        "daily consumption",
        client.fetch_consumos_range(
            p_auth, _since("daily", DAILY_REFRESH_DAYS), today, tipo="diaria",
            keep_partial="daily" not in backfill,
        ),
    )
    if daily_payload:
        daily = parse_daily_response(daily_payload)

    # Whole months: two years back, or the one window that ends this month.
    monthly_since = (
        month_start(today - timedelta(days=BACKFILL_DAYS)) if "monthly" in backfill
        else month_start(today, MONTHLY_WINDOW_MONTHS - 1)
    )
    monthly_payload = await _safe(
        "monthly consumption",
        client.fetch_consumos_range(
            p_auth, monthly_since, today, tipo="mensual",
            keep_partial="monthly" not in backfill,
        ),
    )
    if monthly_payload:
        monthly = parse_monthly_response(monthly_payload)

    hourly_payload = await _safe(
        "hourly consumption",
        client.fetch_consumos_range(
            p_auth, _since("hourly", HOURLY_REFRESH_DAYS), today, tipo="horaria",
            keep_partial="hourly" not in backfill,
        ),
    )
    if hourly_payload:
        hourly = parse_hourly_response(hourly_payload)

    return caudales, daily, monthly, flow, hourly
