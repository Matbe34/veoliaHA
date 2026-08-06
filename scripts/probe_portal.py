"""Diagnose an unsupported Veolia-group portal.

Logs in, then reports the *structure* of what it found — paths, portlet ids,
which JSON blocks exist and their key names. It never prints values, so the
output is safe to paste into a GitHub issue.

Usage:
    VEOLIA_USER='you@example.com' VEOLIA_PASSWORD='...' \
        python scripts/probe_portal.py https://hidrogea.veolia.es
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.veolia_water.parser import (  # noqa: E402
    ParseError,
    extract_json_block,
    parse_caudales_response,
    parse_daily_response,
    parse_inicio,
    parse_monthly_response,
)
from custom_components.veolia_water.portal import profile_for_url  # noqa: E402
from custom_components.veolia_water.veolia_client import (  # noqa: E402
    CDNBlockedError,
    LoginError,
    VeoliaClient,
    VeoliaError,
)

BLOCKS = ("contrato", "miUltimoConsumo", "miUltimoConsumoAnt",
          "miUltimaFactura", "listadoConsumosImportes")

PORTLET_RE = re.compile(r"p_p_id=([A-Za-z0-9_]+)")
LOGIN_FIELD_RE = re.compile(r"name=['\"]_([A-Za-z0-9]+)_(?:login|password)['\"]")
# Excluding whitespace and angle brackets matters: without them a match runs
# past the href and swallows arbitrary page text into the report.
GROUP_LINK_RE = re.compile(r"/group/([^/'\"?#\s<>]+)/([^/'\"?#\s<>]+)")

_secrets: list[str] = []

QUERY_RE = re.compile(r"\?[^\s'\"]*")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
# Contract numbers, meter serials, DNI/NIE and invoice ids are all long digit
# runs. Nothing this script means to report has one: counts are small, years
# are four digits, and sizes are printed in KB.
LONG_DIGITS_RE = re.compile(r"\d{6,}")


def clean(text: str) -> str:
    """Redact anything identifying. Every line of output passes through here."""
    for s in _secrets:
        if s:
            text = text.replace(s, "<redacted>")
    text = QUERY_RE.sub("?<query-stripped>", text)
    text = EMAIL_RE.sub("<email>", text)
    return LONG_DIGITS_RE.sub("<digits>", text)


def say(text: str = "") -> None:
    print(clean(text))


def out(label: str, value) -> None:
    say(f"  {label:<28} {value}")


def describe_blocks(html: str) -> None:
    say("\n## JSON blocks on the landing page")
    for key in BLOCKS:
        block = extract_json_block(html, key)
        if block is None:
            out(key, "MISSING")
        elif isinstance(block, dict):
            out(key, f"object, keys = {sorted(block)}")
        elif isinstance(block, list):
            first = next((i for i in block if isinstance(i, dict)), None)
            out(key, f"array[{len(block)}], item keys = {sorted(first) if first else '?'}")
        else:
            out(key, f"unexpected type {type(block).__name__}")


def describe_structure(html: str, label: str) -> None:
    say(f"\n## Page structure ({label})")
    out("size", f"{len(html) // 1024} KB")
    portlets = sorted(set(PORTLET_RE.findall(html)))
    out("p_p_id values", portlets or "none found")
    login_fields = sorted(set(LOGIN_FIELD_RE.findall(html)))
    if login_fields:
        out("login form portlet", login_fields)
    pages = sorted({f"/group/{s}/{p}" for s, p in GROUP_LINK_RE.findall(html)})
    out("linked pages", f"{len(pages)} found")
    for page in pages[:25]:
        say(f"      {page}")


def check_parses(html: str) -> None:
    """Does the integration's own parser accept this page?"""
    say("\n## Parser result")
    try:
        contract, reading, invoice, history = parse_inicio(html)
    except ParseError as e:
        out("parse_inicio", f"FAILED — {e}")
        return
    out("parse_inicio", "OK")
    out("contract number", "found" if contract.contract_number else "MISSING")
    out("address", "found" if contract.address else "not published")
    out("smart metering", contract.smart_metering
        if contract.smart_metering is not None else "not published")
    out("meter index", "found" if reading.meter_index_m3 is not None else "missing")
    out("period consumption", "found" if reading.consumption_period_m3 else "0 or missing")
    out("invoice amount", "found" if invoice.amount_eur is not None else "missing")
    out("history periods", len(history))


async def check_consumption_api(client: VeoliaClient, p_auth: str) -> None:
    """Actually call the data endpoints — the part page structure can't prove."""
    say("\n## Consumption API")
    today = date.today()
    probes = (
        ("buscarCaudales", client.fetch_caudales(
            p_auth, today - timedelta(days=30), today), parse_caudales_response),
        ("buscarConsumosDiaria", client.fetch_buscar_consumos(
            p_auth, today - timedelta(days=30), today, tipo="diaria"), parse_daily_response),
        ("buscarConsumosMensual", client.fetch_buscar_consumos(
            p_auth, today - timedelta(days=365), today, tipo="mensual"), parse_monthly_response),
    )
    for name, coro, parse in probes:
        try:
            payload = await coro
        except VeoliaError as e:
            out(name, f"FAILED — {e}")
            continue
        if not isinstance(payload, dict):
            out(name, f"unexpected response type {type(payload).__name__}")
            continue
        parsed = parse(payload)
        out(name, f"{len(parsed)} records parsed, response keys = {sorted(payload)}")
        if not parsed:
            for key in ("caudales", "consumos"):
                items = payload.get(key)
                if isinstance(items, list) and items and isinstance(items[0], dict):
                    out(f"  {key}[0] keys", sorted(items[0]))


async def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    base_url = sys.argv[1].rstrip("/")
    user = os.environ.get("VEOLIA_USER")
    password = os.environ.get("VEOLIA_PASSWORD")
    if not (user and password):
        print("Set VEOLIA_USER and VEOLIA_PASSWORD in the environment.")
        return 2
    _secrets.extend([user, password, user.split("@")[0]])

    profile = profile_for_url(base_url)
    say(f"# Portal probe — {urlsplit(base_url).hostname}\n")
    say("## Starting profile")
    out("key", profile.key)
    out("confirmed", profile.confirmed)
    out("site", profile.site or "(to be discovered)")

    async with VeoliaClient(user, password, base_url=base_url, profile=profile) as client:
        say("\n## Login")
        try:
            token = await client.probe_login_page()
        except VeoliaError as e:
            out("login page", f"FAILED — {e}")
            return 1
        out("login page", "reachable" if token is not None else "no p_auth token found")

        try:
            await client.login()
        except CDNBlockedError as e:
            out("result", f"BLOCKED BY CDN — {e}")
            return 1
        except LoginError as e:
            out("result", f"REJECTED — {e}")
            return 1
        out("result", "success")
        out("discovered site", client.profile.site or "NOT FOUND")
        out("inicio path", client.profile.inicio_path or "unknown")

        try:
            html = await client.fetch_inicio()
        except VeoliaError as e:
            out("landing page", f"FAILED — {e}")
            return 1
        describe_structure(html, "landing")
        describe_blocks(html)
        check_parses(html)

        say("\n## Consumption page")
        try:
            consumos = await client.fetch_consumos_page()
        except VeoliaError as e:
            out("result", f"FAILED — {e}")
            return 0
        out("result", "reachable")
        out("consumos portlet", sorted(set(PORTLET_RE.findall(consumos))) or "none")
        p_auth = client.extract_auth_token(consumos)
        out("p_auth present", p_auth is not None)
        if p_auth:
            await check_consumption_api(client, p_auth)

    say("\nDone. This output contains no credentials or meter values — "
          "safe to paste into the issue.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
