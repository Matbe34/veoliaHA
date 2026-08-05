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
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.veolia_water.parser import extract_json_block  # noqa: E402
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
GROUP_LINK_RE = re.compile(r"/group/([^/'\"?#]+)/([^/'\"?#]+)")

_secrets: list[str] = []


def clean(text: str) -> str:
    """Strip credentials and query strings before anything is printed."""
    for s in _secrets:
        if s:
            text = text.replace(s, "<redacted>")
    return re.sub(r"\?[^\s'\"]*", "?<query-stripped>", text)


def out(label: str, value) -> None:
    print(f"  {label:<28} {clean(str(value))}")


def describe_blocks(html: str) -> None:
    print("\n## JSON blocks on the landing page")
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
    print(f"\n## Page structure ({label})")
    out("size", f"{len(html)} bytes")
    portlets = sorted(set(PORTLET_RE.findall(html)))
    out("p_p_id values", portlets or "none found")
    login_fields = sorted(set(LOGIN_FIELD_RE.findall(html)))
    if login_fields:
        out("login form portlet", login_fields)
    pages = sorted({f"/group/{s}/{p}" for s, p in GROUP_LINK_RE.findall(html)})
    out("linked pages", f"{len(pages)} found")
    for page in pages[:25]:
        print(f"      {page}")


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
    print(f"# Portal probe — {urlsplit(base_url).hostname}\n")
    print("## Starting profile")
    out("key", profile.key)
    out("confirmed", profile.confirmed)
    out("site", profile.site or "(to be discovered)")

    async with VeoliaClient(user, password, base_url=base_url, profile=profile) as client:
        print("\n## Login")
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

        print("\n## Consumption page")
        try:
            consumos = await client.fetch_consumos_page()
        except VeoliaError as e:
            out("result", f"FAILED — {e}")
        else:
            out("result", "reachable")
            out("p_auth present", client.extract_auth_token(consumos) is not None)
            out("consumos portlet", sorted(set(PORTLET_RE.findall(consumos))) or "none")

    print("\nDone. This output contains no credentials or meter values — "
          "safe to paste into the issue.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
