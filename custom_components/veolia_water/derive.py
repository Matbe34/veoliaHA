"""Derived metrics computed from the daily smart-meter series.

Pure Python, no HA imports, so it stays unit-testable outside Home Assistant
(same reasoning as `parser`, `models`, `portal` and `veolia_client`).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from .models import DailyConsumption, Reading


def apply_daily_derivations(
    reading: Reading,
    daily: list[DailyConsumption],
    *,
    today: date | None = None,
) -> None:
    """Refresh meter index + derive latest / 7-day / month-to-date metrics."""
    if not daily:
        return
    apply_period_fallback(reading, daily)

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

    if today is None:
        today = datetime.now(timezone.utc).date()
    mtd = [
        d.consumo_m3 for d in daily
        if d.consumo_m3 is not None
        and d.fecha.year == today.year
        and d.fecha.month == today.month
    ]
    if mtd:
        reading.month_to_date_m3 = round(sum(mtd), 3)


def apply_period_fallback(reading: Reading, daily: list[DailyConsumption]) -> None:
    """Stand in for the billing period on contracts that haven't been billed yet.

    A new contract has no closed period, so the portal's `ultimo` block reports
    0 m³ over no days and both period sensors read 0. Treat the whole daily
    series as the period instead — it's the only span we can honestly total.
    A real period is left alone, including a genuine 0 m³ one.
    """
    if reading.period_days:
        return
    totals = [d.consumo_m3 for d in daily if d.consumo_m3 is not None]
    if not totals:
        return
    reading.consumption_period_m3 = round(sum(totals), 3)
    reading.consumption_daily_l = int(round(sum(totals) / len(totals) * 1000))
    reading.period_days = len(totals)
