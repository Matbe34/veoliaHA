"""Per-tenant portal profiles.

Veolia-group portals share one Liferay application but each tenant picks its
own site friendly URL (`/group/<site>/...`) and may rename the portlets.
Only `sorea` is confirmed against a real account; anything a profile doesn't
pin down is discovered at login. `scripts/probe_portal.py` collects the
evidence needed to confirm the others.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Optional
from urllib.parse import urlsplit

DEFAULT_BASE_URL = "https://sorea.veolia.cat"

# Pulls the Liferay site friendly URL out of any portal path.
SITE_PATH_RE = re.compile(r"/group/([^/?#]+)")


@dataclass(frozen=True)
class PortalProfile:
    """Everything that varies between portals. `site=None` means discover it."""

    key: str
    name: str
    base_url: str
    site: Optional[str] = None
    confirmed: bool = False
    login_path: str = "/login"
    inicio_slug: str = "inicio"
    consumos_slug: str = "mis-consumos"
    login_portlet: str = "CustomLoginPortlet"
    consumos_portlet: str = "MisConsumos"
    accept_language: str = "es,ca;q=0.9,en;q=0.8"

    def with_site(self, site: Optional[str]) -> "PortalProfile":
        return replace(self, site=site) if site and site != self.site else self

    def page_path(self, slug: str) -> Optional[str]:
        """Absolute path for a page slug, or None while the site is unknown."""
        return f"/group/{self.site}/{slug}" if self.site else None

    @property
    def inicio_path(self) -> Optional[str]:
        return self.page_path(self.inicio_slug)

    @property
    def consumos_path(self) -> Optional[str]:
        return self.page_path(self.consumos_slug)


PROFILES: tuple[PortalProfile, ...] = (
    PortalProfile(
        key="sorea",
        name="Veolia / Sorea (Catalonia)",
        base_url="https://sorea.veolia.cat",
        site="soreaonline",
        confirmed=True,
        accept_language="ca,es;q=0.9,en;q=0.8",
    ),
    # Unconfirmed (issue #3). Same region/language as Sorea, so its defaults
    # are the likeliest fit.
    PortalProfile(
        key="agbar",
        name="Aigües de Barcelona (Catalonia)",
        base_url="https://agbar.veolia.cat",
        accept_language="ca,es;q=0.9,en;q=0.8",
    ),
    # Unconfirmed (issue #5).
    PortalProfile(
        key="hidrogea",
        name="Hidrogea (Murcia)",
        base_url="https://hidrogea.veolia.es",
    ),
)

GENERIC_PROFILE = PortalProfile(
    key="generic",
    name="Other Veolia portal",
    base_url="",
)


def profile_for_url(base_url: str) -> PortalProfile:
    """Best-matching profile by hostname; unknown hosts get the generic one."""
    host = (urlsplit(base_url).hostname or "").lower().removeprefix("www.")
    for profile in PROFILES:
        if host == (urlsplit(profile.base_url).hostname or "").lower():
            return profile
    return replace(GENERIC_PROFILE, base_url=base_url.rstrip("/"))


def site_from_url(url: str) -> Optional[str]:
    """Extract the Liferay site friendly URL from a portal URL, if present."""
    m = SITE_PATH_RE.search(url or "")
    return m.group(1) if m else None


def apply_overrides(profile: PortalProfile, overrides: dict) -> PortalProfile:
    """Layer non-blank user overrides onto a profile."""
    fields = (
        "site", "login_path", "inicio_slug", "consumos_slug",
        "login_portlet", "consumos_portlet",
    )
    patch = {
        f: str(overrides[f]).strip()
        for f in fields
        if overrides.get(f) is not None and str(overrides[f]).strip()
    }
    return replace(profile, **patch) if patch else profile


# Imperva sits in front of these portals and 403s obvious automation
# defaults, so this mimics a recent Chromium.
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "ca,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-User": "?1",
    "Sec-Fetch-Dest": "document",
    "Sec-Ch-Ua": '"Chromium";v="126", "Not.A/Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Linux"',
}


def build_headers(profile: PortalProfile) -> dict:
    """Request headers for a profile — only the language varies."""
    return {**DEFAULT_HEADERS, "Accept-Language": profile.accept_language}
