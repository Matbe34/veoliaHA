"""Parse Liferay portlet responses + locale-formatted dates/numbers.

The portal serves Spanish / Catalan content. Numbers are `1.234,56`-style;
dates appear in many forms including the Catalan elision `d'abr.` (curly or
straight apostrophe). Everything is fault-tolerant: an unparseable field
yields None rather than raising, so a single missing value never breaks a
whole cycle.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time
from typing import Any, Optional

from .models import (
    Caudal,
    Contract,
    DailyConsumption,
    FlowSummary,
    Invoice,
    MonthlyConsumption,
    Reading,
)

_LOGGER = logging.getLogger(__name__)


class ParseError(Exception):
    """The page didn't contain the structured JSON we depend on."""


_NUMBER_RE = re.compile(
    r"-?\d{1,3}(?:[.\s]\d{3})+(?:,\d+)?|-?\d+(?:[.,]\d+)?"
)

_DATE_PATTERNS = ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y")

_MONTHS = {
    # Spanish abbreviated
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dic": 12,
    # Spanish full
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
    # Catalan abbreviated
    "gen": 1, "febr": 2, "març": 3, "marc": 3, "maig": 5, "juny": 6,
    "ag": 8, "set": 9, "des": 12,
    # Catalan full
    "gener": 1, "febrer": 2, "juliol": 7, "agost": 8,
    "setembre": 9, "novembre": 11, "desembre": 12,
    # English (occasionally seen in caudales)
    "jan": 1, "apr": 4, "aug": 8, "dec": 12,
}


def parse_decimal(text: Any) -> Optional[float]:
    """Parse a Spanish-formatted number: '1.234,56', '12,34', '12.34', '54,32 €'."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    m = _NUMBER_RE.search(str(text))
    if not m:
        return None
    raw = m.group(0)
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def parse_int(text: Any) -> Optional[int]:
    v = parse_decimal(text)
    return int(v) if v is not None else None


def parse_date(text: Any) -> Optional[date]:
    """Numeric date formats only — DD/MM/YYYY, YYYY-MM-DD, etc."""
    if text is None:
        return None
    t = str(text).strip()
    if not t:
        return None
    for fmt in _DATE_PATTERNS:
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    return None


def parse_date_spanish(text: Any) -> Optional[date]:
    """Handle named-month dates: '19 May 2026', '23 de febr. 2026', "30 d'abr. 2026".

    Falls through to `parse_date` for numeric formats.
    """
    d = parse_date(text)
    if d is not None:
        return d
    if text is None:
        return None
    s = str(text).strip().lower()
    # Catalan article elision: straight ' and curly ' (U+2019).
    s = s.replace("'", " ").replace("’", " ")
    s = s.replace(".", "").replace(",", " ")
    s = re.sub(r"\b(?:de[l]?|d)\b", " ", s)
    parts = s.split()
    if len(parts) < 3:
        return None
    day = month = year = None
    for p in parts:
        if p.isdigit():
            n = int(p)
            if 1 <= n <= 31 and day is None:
                day = n
            elif n >= 1900:
                year = n
        else:
            key = next((p[:k] for k in (len(p), 5, 4, 3) if p[:k] in _MONTHS), None)
            if key:
                month = _MONTHS[key]
    if day and month and year:
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def parse_time(text: Any) -> Optional[time]:
    if text is None:
        return None
    s = str(text).strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue
    return None


def _balanced_json(text: str, start: int) -> Optional[str]:
    """Return the JSON literal beginning at `text[start]` (must be '{' or '[')."""
    if start >= len(text) or text[start] not in "{[":
        return None
    open_ch = text[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start: i + 1]
    return None


def extract_json_block(html: str, key: str) -> Optional[Any]:
    """Find the first occurrence of `"<key>": {...}` or `"<key>": [...]` and json.loads it.

    Returns None when the key is absent or the value isn't valid JSON.
    """
    pattern = re.compile(r"""['"]""" + re.escape(key) + r"""['"]\s*:\s*([\[{])""")
    for m in pattern.finditer(html):
        start = m.end() - 1
        blob = _balanced_json(html, start)
        if blob is None:
            continue
        try:
            return json.loads(blob)
        except json.JSONDecodeError:
            continue
    return None


def parse_inicio(html: str) -> tuple[Contract, Reading, Invoice, list[dict]]:
    """Pull the headline data off the /inicio page.

    Raises ParseError when the structured blocks aren't present — usually
    the page rendered as guest because the session was invalid.
    """
    contrato = extract_json_block(html, "contrato")
    ultimo = extract_json_block(html, "miUltimoConsumo")
    factura = extract_json_block(html, "miUltimaFactura")
    historico = extract_json_block(html, "listadoConsumosImportes")

    if not isinstance(contrato, dict):
        raise ParseError("inicio page is missing the `contrato` block")
    if not isinstance(ultimo, dict):
        raise ParseError("inicio page is missing the `miUltimoConsumo` block")

    contract = _build_contract(contrato)
    reading = _build_reading(contract.contract_number, ultimo)
    invoice = (
        _build_invoice(contract.contract_number, factura)
        if isinstance(factura, dict)
        else Invoice(contract_number=contract.contract_number)
    )
    history = historico if isinstance(historico, list) else []
    return contract, reading, invoice, history


def parse_caudales_response(payload: dict) -> list[Caudal]:
    """Newest-first list of daily flow records from buscarCaudales."""
    out: list[Caudal] = []
    if not isinstance(payload, dict):
        return out
    items = payload.get("caudales") or []
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        d = parse_date_spanish(item.get("fecha"))
        if d is None:
            continue
        out.append(Caudal(
            fecha=d,
            q_min_m3h=parse_decimal(item.get("qMin")),
            q_max_m3h=parse_decimal(item.get("qMax")),
            hora_min=parse_time(item.get("horaMin")),
            hora_max=parse_time(item.get("horaMax")),
        ))
    return out


def summarize_flow(caudales: list[Caudal]) -> FlowSummary:
    """Distil 'today's' values + a coarse leak flag from a Caudal list."""
    if not caudales:
        return FlowSummary()
    latest = max(caudales, key=lambda c: c.fecha)
    possible_leak = (
        latest.q_min_m3h > 0 if latest.q_min_m3h is not None else None
    )
    return FlowSummary(
        latest_date=latest.fecha,
        q_max_today_m3h=latest.q_max_m3h,
        q_min_today_m3h=latest.q_min_m3h,
        q_max_time_today=latest.hora_max,
        possible_leak=possible_leak,
        record_count=len(caudales),
    )


def parse_daily_response(payload: dict) -> list[DailyConsumption]:
    """Parse the daily consumption list from buscarConsumosDiaria."""
    out: list[DailyConsumption] = []
    if not isinstance(payload, dict):
        return out
    for item in payload.get("consumos") or []:
        if not isinstance(item, dict):
            continue
        d = parse_date_spanish(item.get("fechaConsumo"))
        if d is None:
            continue
        ct = item.get("consumptionType") or {}
        is_est = bool(item.get("lecturaEstimada")) or (
            isinstance(ct, dict)
            and "estimada" in (ct.get("consumptionClass") or "").lower()
        )
        out.append(DailyConsumption(
            fecha=d,
            hora=parse_time(item.get("horaConsumo")),
            lectura_m3=parse_decimal(item.get("lectura")),
            consumo_m3=parse_decimal(item.get("consumo")),
            is_estimated=is_est,
        ))
    return out


def parse_monthly_response(payload: dict) -> list[MonthlyConsumption]:
    """Parse the monthly list — `fechaConsumo` is '<month-token> <year>'."""
    out: list[MonthlyConsumption] = []
    if not isinstance(payload, dict):
        return out
    for item in payload.get("consumos") or []:
        if not isinstance(item, dict):
            continue
        fc = (item.get("fechaConsumo") or "").strip().lower().replace(".", "")
        parts = fc.split()
        if len(parts) < 2:
            continue
        month_token, year_token = parts[0], parts[-1]
        month = next(
            (_MONTHS[month_token[:k]] for k in (len(month_token), 5, 4, 3)
             if month_token[:k] in _MONTHS),
            None,
        )
        if not month:
            continue
        try:
            year = int(year_token)
        except ValueError:
            continue
        out.append(MonthlyConsumption(
            year=year,
            month=month,
            consumo_m3=parse_decimal(item.get("consumo")),
            is_estimated=bool(item.get("lecturaEstimada")),
        ))
    return out


def _build_contract(blob: dict) -> Contract:
    return Contract(
        contract_number=str(blob.get("number") or "").strip(),
        address=_strip(blob.get("supplyAddress")),
        smart_metering=bool(blob.get("smartMetering")),
        point_of_service_id=_strip(blob.get("pointOfServiceId")),
        last_invoice_status_code=_strip(blob.get("lastInvoiceStatus")),
    )


def _build_reading(contract_number: str, ultimo: dict) -> Reading:
    consumo = parse_decimal(ultimo.get("consumo"))
    numero_dias = parse_int(ultimo.get("numeroDias"))

    monthly_m3: Optional[float] = None
    if consumo is not None and numero_dias and numero_dias > 0:
        monthly_m3 = round(consumo / numero_dias * 30, 3)

    return Reading(
        contract_number=contract_number,
        meter_index_m3=parse_decimal(ultimo.get("lectura")),
        consumption_period_m3=consumo,
        consumption_daily_l=parse_int(ultimo.get("litrosDia")),
        consumption_monthly_m3=monthly_m3,
        last_reading_date=parse_date(ultimo.get("fechaConsumo")),
        reading_type="estimated" if ultimo.get("lecturaEstimada") else "real",
        period_days=numero_dias,
        period_year=parse_int(ultimo.get("anyo")),
        period_number=parse_int(ultimo.get("periodo")),
    )


def _build_invoice(contract_number: str, blob: dict) -> Invoice:
    return Invoice(
        contract_number=contract_number,
        invoice_number=_strip(blob.get("numeroUltimaFactura")),
        amount_eur=parse_decimal(blob.get("importe")),
        status_code=_strip(blob.get("estado")),
        status=_map_invoice_status(blob.get("estado")),
        issue_date=parse_date(blob.get("fechaEmision")),
        period_start=parse_date(blob.get("fechaInicioUltimaFactura")),
        period_end=parse_date(blob.get("fechaFinUltimaFactura")),
    )


def _strip(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


# Status codes seen so far: 3 = paid. Unknown codes pass through as `code_<n>`.
_STATUS_MAP = {"0": "pending", "1": "issued", "2": "sent", "3": "paid", "4": "overdue"}


def _map_invoice_status(code: Any) -> Optional[str]:
    s = _strip(code)
    if s is None:
        return None
    return _STATUS_MAP.get(s, f"code_{s}")
