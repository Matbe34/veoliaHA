from datetime import date, time
from pathlib import Path

import pytest

from custom_components.veolia_water.parser import (
    ParseError,
    extract_json_block,
    parse_caudales_response,
    parse_daily_response,
    parse_date,
    parse_date_spanish,
    parse_decimal,
    parse_inicio,
    parse_int,
    parse_monthly_response,
    parse_time,
    summarize_flow,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ----- scalar helpers ------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1.234,56", 1234.56),
        ("1234,56", 1234.56),
        ("12.34", 12.34),
        ("23,450 m³", 23.450),
        ("54,32 €", 54.32),
        (166.91, 166.91),
        (42, 42.0),
        ("", None),
        (None, None),
        ("nope", None),
    ],
)
def test_parse_decimal(raw, expected):
    assert parse_decimal(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("02/04/2026", date(2026, 4, 2)),
        ("2026-04-02", date(2026, 4, 2)),
        ("02-04-2026", date(2026, 4, 2)),
        ("", None),
        (None, None),
        ("nonsense", None),
    ],
)
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


def test_parse_int_rounds_floats():
    assert parse_int("1.234,56") == 1234
    assert parse_int(None) is None


# ----- JSON block extractor ------------------------------------------------
def test_extract_json_block_basic():
    html = '<script>var x = "foo":{"bar":42,"baz":[1,2]};</script>'
    assert extract_json_block(html, "foo") == {"bar": 42, "baz": [1, 2]}


def test_extract_json_block_handles_strings_with_braces():
    html = '<script>"key":{"a":"a string with } in it","b":2}</script>'
    assert extract_json_block(html, "key") == {"a": "a string with } in it", "b": 2}


def test_extract_json_block_returns_none_when_missing():
    assert extract_json_block("<html></html>", "missing") is None


# ----- end-to-end on the synthetic fixture ---------------------------------
def test_parse_inicio_extracts_full_snapshot():
    contract, reading, invoice, history = parse_inicio(_load("inicio.html"))

    # contract
    assert contract.contract_number == "9999999"
    assert contract.smart_metering is True
    assert contract.address == "CARRER EXEMPLE 1 1-1"
    assert contract.point_of_service_id == "8888888"
    assert contract.last_invoice_status_code == "3"

    # reading
    assert reading.meter_index_m3 == pytest.approx(460.0)
    assert reading.consumption_period_m3 == pytest.approx(39.0)
    assert reading.consumption_daily_l == 443
    assert reading.last_reading_date == date(2026, 2, 23)
    assert reading.reading_type == "real"
    assert reading.period_days == 88
    assert reading.period_year == 2026
    assert reading.period_number == 1
    # Monthly derived = 39 / 88 * 30 = 13.295 m³
    assert reading.consumption_monthly_m3 == pytest.approx(13.295, rel=1e-4)

    # invoice
    assert invoice.invoice_number == "F2026-000001"
    assert invoice.amount_eur == pytest.approx(99.99)
    assert invoice.status == "paid"
    assert invoice.status_code == "3"
    assert invoice.issue_date == date(2026, 3, 2)
    assert invoice.period_start == date(2026, 1, 1)
    assert invoice.period_end == date(2026, 3, 31)

    # history — should have multiple periods
    assert isinstance(history, list)
    assert len(history) >= 4
    assert history[0]["anyo"] == 2026
    assert history[0]["periodo"] == 1


def test_parse_inicio_rejects_empty_html():
    with pytest.raises(ParseError):
        parse_inicio("<html><body><p>nothing here</p></body></html>")


def test_parse_inicio_rejects_html_without_contrato_block():
    html = '<script>var x = "miUltimoConsumo":{"lectura":"100","consumo":"5"};</script>'
    with pytest.raises(ParseError):
        parse_inicio(html)


# ----- caudales (daily flow telemetry) ------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("19 May 2026", date(2026, 5, 19)),
        ("23 de febr. 2026", date(2026, 2, 23)),
        ("1 ene 2025", date(2025, 1, 1)),
        ("18 de Feb 2026", date(2026, 2, 18)),
        # Catalan elision with curly apostrophe (real portal output)
        ("30 d’abr. 2026", date(2026, 4, 30)),
        ("1 d’abr. 2026", date(2026, 4, 1)),
        ("15 d’octubre 2025", date(2025, 10, 15)),
        ("1 d’agost 2024", date(2024, 8, 1)),
        # Catalan elision with straight apostrophe (defensive)
        ("30 d'abr. 2026", date(2026, 4, 30)),
        # Spanish full names
        ("3 de abril de 2026", date(2026, 4, 3)),
        ("nonsense", None),
        (None, None),
    ],
)
def test_parse_date_spanish(raw, expected):
    assert parse_date_spanish(raw) == expected


def test_parse_time():
    assert parse_time("16:01:13") == time(16, 1, 13)
    assert parse_time("16:01") == time(16, 1)
    assert parse_time("") is None
    assert parse_time(None) is None


def test_parse_caudales_response_basic():
    payload = {
        "ultimaPagina": True,
        "caudales": [
            {"fecha": "19 May 2026", "horaMax": "16:01:13", "horaMin": "03:08:51",
             "qMin": "0", "qMax": "0,062"},
            {"fecha": "18 May 2026", "horaMax": "06:01:14", "horaMin": "01:01:07",
             "qMin": "0", "qMax": "0,116"},
        ],
    }
    out = parse_caudales_response(payload)
    assert len(out) == 2
    assert out[0].fecha == date(2026, 5, 19)
    assert out[0].q_min_m3h == 0.0
    assert out[0].q_max_m3h == pytest.approx(0.062)
    assert out[0].hora_max == time(16, 1, 13)
    assert out[0].hora_min == time(3, 8, 51)


def test_parse_caudales_response_skips_unparseable():
    payload = {"caudales": [
        {"fecha": "garbage", "qMax": "1,0"},
        {"fecha": "20 May 2026", "qMax": "2,5", "qMin": "0,1"},
    ]}
    out = parse_caudales_response(payload)
    assert len(out) == 1
    assert out[0].fecha == date(2026, 5, 20)


def test_parse_caudales_response_handles_empty():
    assert parse_caudales_response({"ultimaPagina": True, "caudales": []}) == []
    assert parse_caudales_response({}) == []
    assert parse_caudales_response("nope") == []  # defensive


def test_summarize_flow_picks_latest_and_flags_leak():
    from custom_components.veolia_water.models import Caudal
    cs = [
        Caudal(fecha=date(2026, 5, 18), q_min_m3h=0.0, q_max_m3h=0.1, hora_max=time(6, 0)),
        Caudal(fecha=date(2026, 5, 19), q_min_m3h=0.05, q_max_m3h=0.062, hora_max=time(16, 1, 13)),
    ]
    summary = summarize_flow(cs)
    assert summary.latest_date == date(2026, 5, 19)
    assert summary.q_max_today_m3h == pytest.approx(0.062)
    assert summary.q_min_today_m3h == pytest.approx(0.05)
    assert summary.q_max_time_today == time(16, 1, 13)
    assert summary.possible_leak is True
    assert summary.record_count == 2


def test_summarize_flow_no_leak_when_zero_minimum():
    from custom_components.veolia_water.models import Caudal
    cs = [Caudal(fecha=date(2026, 5, 19), q_min_m3h=0.0, q_max_m3h=0.1)]
    summary = summarize_flow(cs)
    assert summary.possible_leak is False


def test_summarize_flow_empty_list():
    summary = summarize_flow([])
    assert summary.latest_date is None
    assert summary.record_count == 0


# ----- daily / monthly consumption ----------------------------------------
def test_parse_daily_response_basic():
    payload = {
        "ultimaPagina": True,
        "consumos": [
            {"fechaConsumo": "19 de maig 2026", "horaConsumo": "23:06",
             "lectura": "497,822", "consumo": "0,524",
             "consumptionType": {"consumptionLiteral": "REAL", "consumptionClass": "real"},
             "lecturaEstimada": False},
            {"fechaConsumo": "18 de maig 2026", "horaConsumo": "23:18",
             "lectura": "497,298", "consumo": "0,291",
             "consumptionType": {"consumptionClass": "estimada"},
             "lecturaEstimada": True},
        ],
    }
    out = parse_daily_response(payload)
    assert len(out) == 2
    assert out[0].fecha == date(2026, 5, 19)
    assert out[0].lectura_m3 == pytest.approx(497.822, rel=1e-6)
    assert out[0].consumo_m3 == pytest.approx(0.524, rel=1e-6)
    assert out[0].is_estimated is False
    assert out[1].is_estimated is True


def test_parse_daily_response_handles_garbage():
    assert parse_daily_response({"consumos": [{"fechaConsumo": "bogus"}]}) == []
    assert parse_daily_response({}) == []
    assert parse_daily_response("nope") == []


def test_parse_monthly_response_basic():
    payload = {
        "ultimaPagina": True,
        "consumos": [
            {"fechaConsumo": "may 2026", "consumo": "8,373", "lecturaEstimada": False},
            {"fechaConsumo": "abr 2026", "consumo": "14,832", "lecturaEstimada": False},
            {"fechaConsumo": "dic 2025", "consumo": "13,641", "lecturaEstimada": False},
        ],
    }
    out = parse_monthly_response(payload)
    assert len(out) == 3
    assert out[0].year == 2026 and out[0].month == 5
    assert out[0].consumo_m3 == pytest.approx(8.373, rel=1e-4)
    assert out[2].year == 2025 and out[2].month == 12
    assert out[2].consumo_m3 == pytest.approx(13.641, rel=1e-4)
