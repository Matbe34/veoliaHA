"""Hourly history: parsing, windowed/paged fetching, and the cycle's look-back."""
import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from custom_components.veolia_water import veolia_client
from custom_components.veolia_water.cycle import _build_snapshot, _fetch_smart_meter_data, run_cycle
from custom_components.veolia_water.models import Contract, Invoice, Reading
from custom_components.veolia_water.parser import parse_hourly_response
from custom_components.veolia_water.veolia_client import (
    CDNBlockedError,
    PortalHTTPError,
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
        fail=lambda n: PortalHTTPError("buscarConsumosHoraria", 504) if n <= 2 else None,
    )
    out = _run(_client(calls).fetch_consumos_range("t", date(2026, 5, 1), date(2026, 5, 1), tipo="horaria"))
    assert len(out["consumos"]) == 1
    assert len(calls.calls) == 3


@pytest.mark.parametrize("error", [
    PortalHTTPError("buscarConsumosHoraria", 400),
    VeoliaError("proxy said: returned HTTP 503"),  # only the status counts, not the text
])
def test_only_a_5xx_status_is_retried(error):
    calls = _Calls(fail=lambda n: error)
    with pytest.raises(VeoliaError):
        _run(_client(calls).fetch_consumos_range("t", date(2026, 5, 1), date(2026, 5, 1), tipo="horaria"))
    assert len(calls.calls) == 1


def test_a_5xx_that_persists_gives_up_after_three_tries():
    calls = _Calls(fail=lambda n: PortalHTTPError("buscarConsumosHoraria", 504))
    with pytest.raises(PortalHTTPError):
        _run(_client(calls).fetch_consumos_range("t", date(2026, 5, 1), date(2026, 5, 1), tipo="horaria"))
    assert len(calls.calls) == 3


def test_paging_stops_after_four_pages_even_if_the_portal_never_says_last():
    class Endless(_Calls):
        async def __call__(self, p_auth, start, end, *, tipo, inicio, fin):
            self.calls.append((start, end, tipo, inicio, fin))
            return {"consumos": [_row("1 may 2026", "01:59")] * 500, "ultimaPagina": False}

    calls = Endless()
    out = _run(_client(calls).fetch_consumos_range("t", date(2026, 5, 1), date(2026, 5, 1), tipo="horaria"))
    assert len(calls.calls) == 4
    assert len(out["consumos"]) == 2000


def test_monthly_windows_hold_whole_months():
    calls = _Calls({date(2025, 6, 1): [{"fechaConsumo": "jun 2025"}]})
    _run(_client(calls).fetch_consumos_range("t", date(2024, 11, 27), date(2026, 11, 27), tipo="mensual"))
    windows = [(s, e) for s, e, *_ in calls.calls]
    assert windows == [
        (date(2026, 1, 1), date(2026, 11, 27)),
        (date(2025, 2, 1), date(2025, 12, 31)),
        (date(2024, 11, 1), date(2025, 1, 31)),
    ]
    assert all((e - s).days + 1 <= 335 for s, e in windows)


def test_monthly_refresh_is_a_single_request():
    calls = _Calls({date(2026, 11, 1): [{"fechaConsumo": "nov 2026"}]})
    _run(_client(calls).fetch_consumos_range("t", date(2026, 1, 15), date(2026, 11, 27), tipo="mensual"))
    assert [(s, e) for s, e, *_ in calls.calls] == [(date(2026, 1, 1), date(2026, 11, 27))]


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


def test_a_backfill_that_fails_in_an_older_window_returns_nothing():
    # Stored history is never fetched again, so a partial backfill would stay partial.
    calls = _Calls(
        {date(2026, 5, 1): [_row("1 may 2026", "01:59")]},
        fail=lambda n: VeoliaError("returned HTTP 400") if n == 2 else None,
    )
    with pytest.raises(VeoliaError):
        _run(_client(calls).fetch_consumos_range(
            "t", date(2026, 1, 1), date(2026, 5, 1), tipo="horaria", keep_partial=False))


def test_failure_in_the_first_window_raises():
    calls = _Calls(fail=lambda n: VeoliaError("returned HTTP 400"))
    with pytest.raises(VeoliaError):
        _run(_client(calls).fetch_consumos_range("t", date(2026, 5, 1), date(2026, 5, 1), tipo="horaria"))


# ----- the cycle's look-back --------------------------------------------------
class _FakeClient:
    def __init__(self):
        self.starts = {}
        self.keep_partial = {}

    async def fetch_consumos_page(self):
        return "page"

    @staticmethod
    def extract_auth_token(html):
        return "token"

    async def fetch_caudales(self, *args):
        return {}

    async def fetch_consumos_range(self, p_auth, start, end, *, tipo, keep_partial=True):
        self.starts[tipo] = start
        self.keep_partial[tipo] = keep_partial
        if tipo == "horaria":
            return {"consumos": [_row("1 may 2026", "01:59")]}
        return {}


ALL_SERIES = {"hourly", "daily", "monthly"}


def _lookback(client):
    today = datetime.now(timezone.utc).date()
    return {tipo: today - start for tipo, start in client.starts.items()}


def test_series_with_no_stored_history_go_back_two_years():
    client = _FakeClient()
    *_, hourly = _run(_fetch_smart_meter_data(client, True, backfill=ALL_SERIES))
    assert len(hourly) == 1
    back = _lookback(client)
    assert back["horaria"] == back["diaria"] == timedelta(days=730)
    # Monthly snaps to the 1st of the month two years back.
    assert client.starts["mensual"].day == 1
    assert timedelta(days=730) <= back["mensual"] < timedelta(days=761)


def test_series_already_stored_only_refresh_the_recent_window():
    client = _FakeClient()
    _run(_fetch_smart_meter_data(client, True))
    back = _lookback(client)
    assert back["horaria"] == timedelta(days=7)
    assert back["diaria"] == timedelta(days=100)
    today = datetime.now(timezone.utc).date()
    start = client.starts["mensual"]
    # 11 whole months, this one included: one request.
    assert start.day == 1
    assert (today.year - start.year) * 12 + today.month - start.month == 10


def test_each_series_backfills_on_its_own():
    client = _FakeClient()
    _run(_fetch_smart_meter_data(client, True, backfill={"hourly"}))
    back = _lookback(client)
    assert back["horaria"] == timedelta(days=730)
    assert back["diaria"] == timedelta(days=100)
    # Only a backfill must be complete; a refresh keeps what it could read.
    assert client.keep_partial == {"horaria": False, "diaria": True, "mensual": True}


def test_the_cycle_asks_which_series_are_stored_for_the_contract_shown():
    asked = []

    async def stored(number, series):
        asked.append((number, series))
        return series != "daily"

    client = _FakeClient()
    _run(_build_snapshot(client, _parsed(None), stored))
    assert sorted(asked) == [("1", "daily"), ("1", "hourly"), ("1", "monthly")]
    back = _lookback(client)
    assert back["diaria"] == timedelta(days=730)
    assert back["horaria"] == timedelta(days=7)


def test_without_a_stored_lookup_nothing_is_backfilled():
    class Portal(_FakeClient):
        async def login(self):
            pass

        async def fetch_inicio(self):
            return '<script>var x = {"contrato":{"number":"1","smartMetering":true}};</script>'

    client = Portal()
    _run(run_cycle(client, []))
    assert _lookback(client)["horaria"] == timedelta(days=7)


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
        async def fetch_consumos_range(self, p_auth, start, end, *, tipo, **_):
            if tipo == "horaria":
                return {"consumos": [_row("23 nov 2026", "16:59", "172,420")]}
            return {}

    snap = _run(_build_snapshot(Client(), _parsed(landing)))
    assert snap.reading.meter_index_m3 == expected


def test_the_latest_hour_beats_the_daily_reading(monkeypatch):
    """The daily series reports the reading at the start of the day."""
    import custom_components.veolia_water.cycle as cycle

    def stale_daily(reading, daily):
        reading.meter_index_m3 = 172.336

    monkeypatch.setattr(cycle, "apply_daily_derivations", stale_daily)

    class Client(_FakeClient):
        async def fetch_consumos_range(self, p_auth, start, end, *, tipo, **_):
            if tipo == "horaria":
                return {"consumos": [_row("23 nov 2026", "16:59", "172,420")]}
            return {}

    snap = _run(_build_snapshot(Client(), _parsed(None)))
    assert snap.reading.meter_index_m3 == 172.42
    assert snap.reading.last_reading_date == date(2026, 11, 23)


def test_non_smart_meters_fetch_nothing():
    client = _FakeClient()
    *_, hourly = _run(_fetch_smart_meter_data(client, False, backfill=ALL_SERIES))
    assert hourly == [] and client.starts == {}
