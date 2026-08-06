"""Derived metrics from the daily series, including the unbilled-contract fallback."""
from datetime import date

import pytest

from custom_components.veolia_water.derive import (
    apply_daily_derivations,
    apply_period_fallback,
)
from custom_components.veolia_water.models import DailyConsumption, Reading


def _reading(**kw) -> Reading:
    return Reading(contract_number="12348720", **kw)


def _daily(*rows) -> list[DailyConsumption]:
    return [DailyConsumption(fecha=d, consumo_m3=c, lectura_m3=l) for d, c, l in rows]


SERIES = _daily(
    (date(2026, 8, 3), 0.089, 186.304),
    (date(2026, 8, 4), 0.060, 186.364),
    (date(2026, 8, 5), 0.135, 186.499),
)


# ----- period fallback -----------------------------------------------------
def test_period_fallback_totals_whole_series_when_never_billed():
    r = _reading(consumption_period_m3=0.0, consumption_daily_l=0, period_days=None)
    apply_period_fallback(r, SERIES)
    assert r.consumption_period_m3 == pytest.approx(0.284)
    assert r.consumption_daily_l == 95  # 0.284 / 3 days, in litres
    assert r.period_days == 3


def test_period_fallback_leaves_a_real_period_alone():
    r = _reading(consumption_period_m3=12.5, consumption_daily_l=140, period_days=89)
    apply_period_fallback(r, SERIES)
    assert r.consumption_period_m3 == 12.5
    assert r.consumption_daily_l == 140
    assert r.period_days == 89


def test_period_fallback_keeps_a_genuine_zero_period():
    """0 m³ over a real 89-day period is a fact, not missing data."""
    r = _reading(consumption_period_m3=0.0, consumption_daily_l=0, period_days=89)
    apply_period_fallback(r, SERIES)
    assert r.consumption_period_m3 == 0.0
    assert r.period_days == 89


def test_period_fallback_ignores_rows_without_consumption():
    r = _reading(period_days=None)
    apply_period_fallback(r, _daily((date(2026, 8, 3), None, 1.0), (date(2026, 8, 4), 0.2, 1.2)))
    assert r.consumption_period_m3 == pytest.approx(0.2)
    assert r.period_days == 1


def test_period_fallback_noop_when_no_consumption_at_all():
    r = _reading(period_days=None)
    apply_period_fallback(r, _daily((date(2026, 8, 3), None, 1.0)))
    assert r.consumption_period_m3 is None
    assert r.period_days is None


# ----- the rest of the derivations -----------------------------------------
def test_daily_derivations_use_the_newest_row():
    r = _reading()
    apply_daily_derivations(r, SERIES, today=date(2026, 8, 6))
    assert r.meter_index_m3 == pytest.approx(186.499)
    assert r.last_reading_date == date(2026, 8, 5)
    assert r.latest_daily_consumption_l == 135
    assert r.rolling_7d_avg_l == 95
    assert r.month_to_date_m3 == pytest.approx(0.284)


def test_month_to_date_only_counts_the_current_month():
    r = _reading()
    series = _daily(
        (date(2026, 7, 31), 1.0, 10.0),
        (date(2026, 8, 1), 0.25, 10.25),
    )
    apply_daily_derivations(r, series, today=date(2026, 8, 6))
    assert r.month_to_date_m3 == pytest.approx(0.25)


def test_daily_derivations_noop_on_empty_series():
    r = _reading()
    apply_daily_derivations(r, [], today=date(2026, 8, 6))
    assert r.meter_index_m3 is None
    assert r.period_days is None
