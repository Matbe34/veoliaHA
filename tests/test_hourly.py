"""Hourly history: parsing, windowed/paged fetching, and the cycle's look-back."""
import asyncio
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from custom_components.veolia_water import veolia_client
from custom_components.veolia_water.cycle import _build_snapshot, _fetch_smart_meter_data
from custom_components.veolia_water.models import Contract, Invoice, Reading
from custom_components.veolia_water.parser import parse_hourly_response
from custom_components.veolia_water.veolia_client import (
    CDNBlockedError,
    VeoliaClient,
    VeoliaError,
)

FIXTURE = Path(__file__).parent / "fixtures" / "viaqua_sample.json"


def _row(day, hour, lectura="1,000", consumo="0,001", **extra):
    return {
        "fechaConsumo": day,
        "horaConsumo": hour,
        "lectura": lectura,
        "consumo": consumo,
        "consumptionType": {"consumptionLiteral": "REAL"},
        "lecturaEstimada": False,
        **extra,
    }


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


# ----- parse_hourly_response -----------------------------------------------
def test_hour_is_floored_to_its_start_and_converted_to_utc():
    # 08:59 in November (UTC+1) closes the hour that began at 08:00 local.
    out = parse_hourly_response({"consumos": [_row("23 nov 2026", "08:59", "172,343", "0,004")]})
    assert [(h.start, h.lectura_m3, h.consumo_m3) for h in out] == [
        (_utc(2026, 11, 23, 7), 172.343, 0.004)
    ]


def test_summer_offset_and_the_odd_minute_58():
    out = parse_hourly_response({"consumos": [_row("15 jul 2026", "10:58")]})
    assert out[0].start == _utc(2026, 7, 15, 8)


def test_returns_oldest_first_whatever_the_input_order():
    rows = [_row("2 dic 2026", "01:59"), _row("1 dic 2026", "23:59"), _row("2 dic 2026", "00:59")]
    out = parse_hourly_response({"consumos": rows})
    assert [h.start for h in out] == sorted(h.start for h in out)
    assert len(out) == 3


def test_estimated_flags():
    rows = [
        _row("1 dic 2026", "01:59", lecturaEstimada=True),
        _row("1 dic 2026", "02:59", consumptionType={"consumptionLiteral": "ESTIMADA"}),
        _row("1 dic 2026", "03:59"),
    ]
    assert [h.is_estimated for h in parse_hourly_response({"consumos": rows})] == [True, True, False]


def test_autumn_dst_repeated_hour_keeps_both_occurrences():
    # Newest first, like the portal: 02:59 shows up twice on 25 Oct 2026.
    rows = [
        _row("25 oct 2026", "03:59", "10,003"),
        _row("25 oct 2026", "02:59", "10,002"),  # second pass, UTC+1
        _row("25 oct 2026", "02:59", "10,001"),  # first pass, UTC+2
        _row("25 oct 2026", "01:59", "10,000"),
    ]
    out = parse_hourly_response({"consumos": rows})
    assert [(h.start, h.lectura_m3) for h in out] == [
        (_utc(2026, 10, 24, 23), 10.000),
        (_utc(2026, 10, 25, 0), 10.001),
        (_utc(2026, 10, 25, 1), 10.002),
        (_utc(2026, 10, 25, 2), 10.003),
    ]


def test_real_portal_sample_has_23_and_25_hour_days():
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))["hourly"]
    out = parse_hourly_response({"consumos": rows})
    assert len(out) == len(rows)  # nothing collapsed or dropped
    spring = [h for h in out if _utc(2026, 3, 28, 23) <= h.start < _utc(2026, 3, 29, 22)]
    autumn = [h for h in out if _utc(2026, 10, 24, 22) <= h.start < _utc(2026, 10, 25, 23)]
    assert len(spring) == 23
    assert len(autumn) == 25
    readings = [h.lectura_m3 for h in out]
    assert readings == sorted(readings)  # the meter never runs backwards


@pytest.mark.parametrize("payload", [None, {}, {"consumos": None}, {"consumos": ["x", {}]}])
def test_garbage_payloads_give_nothing(payload):
    assert parse_hourly_response(payload) == []


# ----- fetch_consumos_range --------------------------------------------------
class _Calls:
    """Stands in for `fetch_buscar_consumos`, serving rows from a day -> rows map."""

    def __init__(self, rows_by_day=None, *, fail=None):
        self.rows_by_day = rows_by_day or {}
        self.fail = fail or (lambda call_no: None)
        self.calls = []

    async def __call__(self, p_auth, start, end, *, tipo, inicio, fin):
        self.calls.append((start, end, tipo, inicio, fin))
        error = self.fail(len(self.calls))
        if error:
            raise error
        rows = [
            r for d, rs in sorted(self.rows_by_day.items(), reverse=True)
            if start <= d <= end for r in rs
        ]
        page = rows[inicio: fin + 1]  # `fin` is inclusive
        return {"consumos": page, "ultimaPagina": fin + 1 >= len(rows)}


def _client(calls) -> VeoliaClient:
    client = VeoliaClient.__new__(VeoliaClient)
    client.fetch_buscar_consumos = calls
    return client


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def instant(_):
        return None

    monkeypatch.setattr(veolia_client.asyncio, "sleep", instant)


def test_hourly_range_is_cut_into_40_day_windows_newest_first():
    calls = _Calls({
        date(2026, 4, 10): [_row("10 abr 2026", "01:59")],
        date(2026, 3, 1): [_row("1 mar 2026", "01:59")],
        date(2026, 1, 1): [_row("1 ene 2026", "01:59")],
    })
    _run(_client(calls).fetch_consumos_range("t", date(2026, 1, 1), date(2026, 4, 10), tipo="horaria"))
    windows = [(s, e) for s, e, *_ in calls.calls]
    assert windows == [
        (date(2026, 3, 2), date(2026, 4, 10)),
        (date(2026, 1, 21), date(2026, 3, 1)),
        (date(2026, 1, 1), date(2026, 1, 20)),
    ]
    assert all((e - s).days + 1 <= 40 for s, e in windows)


def test_daily_and_monthly_use_long_windows():
    for tipo in ("diaria", "mensual"):
        calls = _Calls({date(2025, 1, 1): [{"fechaConsumo": "x"}]})
        _run(_client(calls).fetch_consumos_range("t", date(2025, 1, 1), date(2026, 1, 1), tipo=tipo))
        assert all((e - s).days + 1 <= 360 for s, e, *_ in calls.calls)
        assert len(calls.calls) == 2


def test_pages_use_an_inclusive_fin_and_lose_no_rows():
    day = date(2026, 5, 1)
    rows = [_row("1 may 2026", f"{h:02d}:59", consumo=f"{h},000") for h in range(24)]
    rows = rows * 25  # 600 rows -> two pages of 500
    calls = _Calls({day: rows})
    out = _run(_client(calls).fetch_consumos_range("t", day, day, tipo="horaria"))
    assert len(out["consumos"]) == 600
    assert [(i, f) for *_, i, f in calls.calls] == [(0, 499), (500, 999)]


def test_stops_after_two_empty_windows_before_the_meter_existed():
    calls = _Calls({date(2026, 3, 3): [_row("3 mar 2026", "10:59")]})
    out = _run(_client(calls).fetch_consumos_range(
        "t", date(2024, 1, 1), date(2026, 4, 1), tipo="horaria"))
    assert len(out["consumos"]) == 1
    # 2 windows with data/edge + 2 empty ones, not ~17 windows back to 2024.
    assert len(calls.calls) <= 4


def test_transient_5xx_is_retried():
    calls = _Calls(
        {date(2026, 5, 1): [_row("1 may 2026", "01:59")]},
        fail=lambda n: VeoliaError("buscarConsumosHoraria returned HTTP 504") if n <= 2 else None,
    )
    out = _run(_client(calls).fetch_consumos_range("t", date(2026, 5, 1), date(2026, 5, 1), tipo="horaria"))
    assert len(out["consumos"]) == 1
    assert len(calls.calls) == 3


def test_other_errors_are_not_retried_and_cdn_blocks_propagate():
    calls = _Calls(fail=lambda n: CDNBlockedError("blocked"))
    with pytest.raises(CDNBlockedError):
        _run(_client(calls).fetch_consumos_range("t", date(2026, 5, 1), date(2026, 5, 1), tipo="horaria"))
    assert len(calls.calls) == 1

    calls = _Calls(fail=lambda n: VeoliaError("returned non-JSON body"))
    with pytest.raises(VeoliaError):
        _run(_client(calls).fetch_consumos_range("t", date(2026, 5, 1), date(2026, 5, 1), tipo="horaria"))
    assert len(calls.calls) == 1


def test_failure_in_an_older_window_keeps_what_was_already_read():
    calls = _Calls(
        {date(2026, 5, 1): [_row("1 may 2026", "01:59")]},
        fail=lambda n: VeoliaError("returned HTTP 400") if n == 2 else None,
    )
    out = _run(_client(calls).fetch_consumos_range("t", date(2026, 1, 1), date(2026, 5, 1), tipo="horaria"))
    assert len(out["consumos"]) == 1


def test_failure_in_the_first_window_raises():
    calls = _Calls(fail=lambda n: VeoliaError("returned HTTP 400"))
    with pytest.raises(VeoliaError):
        _run(_client(calls).fetch_consumos_range("t", date(2026, 5, 1), date(2026, 5, 1), tipo="horaria"))


# ----- the cycle's look-back --------------------------------------------------
class _FakeClient:
    def __init__(self):
        self.ranges = []

    async def fetch_consumos_page(self):
        return "page"

    @staticmethod
    def extract_auth_token(html):
        return "token"

    async def fetch_caudales(self, *args):
        return {}

    async def fetch_consumos_range(self, p_auth, start, end, *, tipo):
        self.ranges.append((tipo, (end - start).days))
        if tipo == "horaria":
            return {"consumos": [_row("1 may 2026", "01:59")]}
        return {}


def test_first_read_goes_back_two_years_then_only_a_week_of_hours():
    client = _FakeClient()
    *_, hourly = _run(_fetch_smart_meter_data(client, True, full_history=True))
    assert len(hourly) == 1
    assert dict(client.ranges) == {"diaria": 730, "mensual": 730, "horaria": 730}

    client = _FakeClient()
    _run(_fetch_smart_meter_data(client, True, full_history=False))
    assert dict(client.ranges) == {"diaria": 100, "mensual": 360, "horaria": 7}


def _parsed(index):
    return (
        Contract("1", smart_metering=True),
        Reading("1", meter_index_m3=index),
        Invoice("1"),
        [],
    )


@pytest.mark.parametrize("landing,expected", [(172.336, 172.42), (None, 172.42), (180.0, 180.0)])
def test_meter_index_follows_the_latest_hour_unless_it_would_go_backwards(landing, expected):
    class Client(_FakeClient):
        async def fetch_consumos_range(self, p_auth, start, end, *, tipo):
            if tipo == "horaria":
                return {"consumos": [_row("23 nov 2026", "16:59", "172,420")]}
            return {}

    snap = _run(_build_snapshot(Client(), _parsed(landing), set()))
    assert snap.reading.meter_index_m3 == expected


def test_the_latest_hour_beats_the_daily_reading(monkeypatch):
    """The daily series reports the reading at the start of the day."""
    import custom_components.veolia_water.cycle as cycle

    def stale_daily(reading, daily):
        reading.meter_index_m3 = 172.336

    monkeypatch.setattr(cycle, "apply_daily_derivations", stale_daily)

    class Client(_FakeClient):
        async def fetch_consumos_range(self, p_auth, start, end, *, tipo):
            if tipo == "horaria":
                return {"consumos": [_row("23 nov 2026", "16:59", "172,420")]}
            return {}

    snap = _run(_build_snapshot(Client(), _parsed(None), set()))
    assert snap.reading.meter_index_m3 == 172.42
    assert snap.reading.last_reading_date == date(2026, 11, 23)


def test_non_smart_meters_fetch_nothing():
    client = _FakeClient()
    *_, hourly = _run(_fetch_smart_meter_data(client, False, full_history=True))
    assert hourly == [] and client.ranges == []
