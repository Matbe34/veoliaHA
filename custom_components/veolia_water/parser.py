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


def _decimal_or_zero(text: Any) -> float:
    """Like `parse_decimal`, but an absent/unparseable value counts as zero."""
    v = parse_decimal(text)
    return 0.0 if v is None else v


def _int_or_zero(text: Any) -> int:
    v = parse_int(text)
    return 0 if v is None else v


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


def _as_dict(value: Any) -> Optional[dict]:
    """Coerce an extracted block to a dict, or None if there isn't one.

    Some portal variants wrap a single-object block in an array; take the
    first object in that case rather than treating the block as absent.
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return next((item for item in value if isinstance(item, dict)), None)
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

    Only `contrato` is mandatory: without a contract number we can't build
    stable entity ids, and its absence means the page rendered as guest
    because the session was invalid — exactly the case a retry fixes.

    Every other block is optional. Accounts with no billed period yet, or
    with a portal layout that omits a block, get zeroed consumption values
    and a warning instead of a failed setup.
    """
    contrato = _as_dict(extract_json_block(html, "contrato"))
    ultimo = _as_dict(extract_json_block(html, "miUltimoConsumo"))
    factura = _as_dict(extract_json_block(html, "miUltimaFactura"))
    historico = extract_json_block(html, "listadoConsumosImportes")

    # Agbar's landing page carries no `contrato` block, but `miUltimaFactura`
    # repeats the contract number, which is all we need to key the entities.
    contract = _build_contract(contrato or {}, factura)
    if not contract.contract_number:
        raise ParseError(
            "inicio page has no contract number in either `contrato` or "
            "`miUltimaFactura` — the session probably rendered as guest"
        )
    if contrato is None:
        _LOGGER.warning(
            "inicio page has no `contrato` block for contract %s — address and "
            "smart-metering flag unknown.",
            contract.contract_number,
        )

    if ultimo is None:
        _LOGGER.warning(
            "inicio page has no `miUltimoConsumo` block for contract %s — "
            "treating this period's consumption as zero.",
            contract.contract_number,
        )
    reading = _build_reading(contract.contract_number, ultimo or {})

    if factura is None:
        _LOGGER.debug(
            "inicio page has no `miUltimaFactura` block for contract %s.",
            contract.contract_number,
        )
    invoice = (
        _build_invoice(contract.contract_number, factura)
        if factura is not None
        else Invoice(contract_number=contract.contract_number)
    )

    history = (
        [item for item in historico if isinstance(item, dict)]
        if isinstance(historico, list)
        else []
    )
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
            and "estimada" in str(ct.get("consumptionClass") or "").lower()
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
        fc = str(item.get("fechaConsumo") or "").strip().lower().replace(".", "")
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


# The portal labels the contract number differently depending on the block.
_CONTRACT_NUMBER_KEYS = ("number", "numeroContrato", "contractNumber")


def _contract_number_from(blob: Optional[dict]) -> str:
    if not isinstance(blob, dict):
        return ""
    for key in _CONTRACT_NUMBER_KEYS:
        number = str(blob.get(key) or "").strip()
        if number:
            return number
    return ""


def _build_contract(blob: dict, factura: Optional[dict] = None) -> Contract:
    # `miUltimaFactura` repeats the contract number, so it covers a `contrato`
    # block that arrives without one.
    number = _contract_number_from(blob) or _contract_number_from(factura)
    smart = blob.get("smartMetering")
    return Contract(
        contract_number=number,
        address=_strip(blob.get("supplyAddress")),
        # None = unknown; the caller probes the smart-meter endpoints anyway.
        smart_metering=bool(smart) if smart is not None else None,
        point_of_service_id=_strip(blob.get("pointOfServiceId")),
        last_invoice_status_code=_strip(blob.get("lastInvoiceStatus")),
    )


def _build_reading(contract_number: str, ultimo: dict) -> Reading:
    # Consumption quantities default to 0 — "the portal didn't report any" and
    # "none was used" are the same thing for a totaliser. The meter index is
    # deliberately left None: it's `total_increasing`, so a fabricated 0 would
    # look like a meter reset and double-count the whole index in long-term
    # statistics. Same reasoning for the dates and the period identifiers,
    # where 0 isn't a meaningful value at all.
    consumo = _decimal_or_zero(ultimo.get("consumo"))
    numero_dias = parse_int(ultimo.get("numeroDias"))

    monthly_m3 = (
        round(consumo / numero_dias * 30, 3)
        if numero_dias and numero_dias > 0
        else 0.0
    )

    return Reading(
        contract_number=contract_number,
        meter_index_m3=parse_decimal(ultimo.get("lectura")),
        consumption_period_m3=consumo,
        consumption_daily_l=_int_or_zero(ultimo.get("litrosDia")),
        consumption_monthly_m3=monthly_m3,
        last_reading_date=parse_date(ultimo.get("fechaConsumo")),
        reading_type=(
            ("estimated" if ultimo.get("lecturaEstimada") else "real")
            if ultimo
            else None
        ),
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
