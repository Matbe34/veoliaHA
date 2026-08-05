"""HTTP client for the Veolia / Sorea Liferay portal.

Lives outside HA's shared aiohttp session because we need full control of
cookies and headers — the portal sits behind Imperva and is sensitive to
shared/leaky state.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional
from urllib.parse import urlencode, urlsplit

import aiohttp

from .portal import (
    DEFAULT_BASE_URL,
    PortalProfile,
    build_headers,
    profile_for_url,
    site_from_url,
)

_LOGGER = logging.getLogger(__name__)

# Liferay sets these on successful auth — a tenant-independent login signal.
_AUTH_COOKIES = ("ID", "COMPANY_ID", "USER_UUID", "LFR_SESSION_STATE")

_LOGIN_FORM_RE = re.compile(r"""<input[^>]+type=['"]password['"]""", re.I)


class VeoliaError(Exception):
    """Base error for any non-recoverable portal failure."""


class LoginError(VeoliaError):
    """Authentication was rejected — credentials wrong or portal markup changed."""


class CDNBlockedError(VeoliaError):
    """The CDN in front of the portal returned 403 or 429."""


class SessionExpiredError(VeoliaError):
    """A request landed back on /login mid-cycle."""


_AUTH_TOKEN_RE = re.compile(
    r"""(?:Liferay\.authToken\s*=\s*['"](?P<a>[A-Za-z0-9]+)['"]|p_auth=(?P<b>[A-Za-z0-9]+))"""
)


class VeoliaClient:
    """Async client. One instance per cycle; we don't try to persist sessions
    because Liferay's idle timeout (~30 min) is shorter than typical polling.
    """

    def __init__(
        self,
        username: str,
        password: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        profile: Optional[PortalProfile] = None,
        timeout: float = 30.0,
    ) -> None:
        self._username = username
        self._password = password
        self._base_url = base_url.rstrip("/")
        self._profile = profile or profile_for_url(self._base_url)
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: Optional[aiohttp.ClientSession] = None
        self._landing_html: Optional[str] = None

    @property
    def profile(self) -> PortalProfile:
        """The profile in use, including anything learned during login."""
        return self._profile

    async def __aenter__(self) -> "VeoliaClient":
        self._session = aiohttp.ClientSession(
            headers=build_headers(self._profile),
            cookie_jar=aiohttp.CookieJar(unsafe=False),
            timeout=self._timeout,
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    @staticmethod
    def extract_auth_token(html: str) -> Optional[str]:
        m = _AUTH_TOKEN_RE.search(html)
        if not m:
            return None
        return m.group("a") or m.group("b")

    async def login(self) -> None:
        profile = self._profile
        status, _, html = await self._get(f"{self._base_url}{profile.login_path}")
        if status != 200:
            raise LoginError(f"Login page returned HTTP {status}")
        p_auth = self.extract_auth_token(html)
        if not p_auth:
            raise LoginError("Could not find p_auth token on login page")

        portlet = profile.login_portlet
        action_qs = urlencode({
            "p_p_id": portlet,
            "p_p_lifecycle": "1",
            "p_p_state": "normal",
            "p_p_mode": "view",
            f"_{portlet}_javax.portlet.action": "/login/login",
            f"_{portlet}_mvcRenderCommandName": "/login/login",
            "p_auth": p_auth,
        })
        form = {
            f"_{portlet}_login": self._username,
            f"_{portlet}_password": self._password,
            f"_{portlet}_lastContract": "",
            "doActionAfterLogin": "false",
            "saveLastPath": "false",
            "idiomasExcluidosId": "",
        }
        # Unknown tenant: let the portal choose, then learn the site from it.
        if profile.inicio_path:
            form["redirect"] = profile.inicio_path

        status, final_url, body = await self._post(
            f"{self._base_url}{profile.login_path}?{action_qs}", form
        )
        if status >= 400:
            raise LoginError(f"Login POST returned HTTP {status}")
        if not self._is_authenticated(final_url, body):
            raise LoginError(f"Authentication rejected (landed on {final_url})")

        self._landing_html = body
        learned = site_from_url(final_url)
        if learned and learned != profile.site:
            _LOGGER.debug("Learned Liferay site '%s' from %s", learned, final_url)
            self._profile = profile.with_site(learned)
        _LOGGER.info("Authenticated against %s", self._base_url)

    def _is_authenticated(self, final_url: str, html: str) -> bool:
        """Did the login POST work? Cookie first, login-form fallback."""
        if self._has_auth_cookie():
            return True
        return not (self._is_login_url(final_url) or _LOGIN_FORM_RE.search(html))

    def _is_login_url(self, url: str) -> bool:
        """Path-segment match, so a site named `loginonline` isn't a login page."""
        path = urlsplit(url).path.rstrip("/")
        login = self._profile.login_path.rstrip("/")
        return path == login or path.startswith(f"{login}/")

    def _has_auth_cookie(self) -> bool:
        assert self._session is not None
        return any(
            cookie.key in _AUTH_COOKIES and cookie.value
            for cookie in self._session.cookie_jar
        )

    async def fetch_inicio(self) -> str:
        # The login POST already returned this page.
        landing, self._landing_html = self._landing_html, None
        if landing and '"contrato"' in landing:
            return landing
        path = self._profile.inicio_path
        if path is None:
            raise VeoliaError(
                f"Don't know the landing-page path for {self._base_url}. Run "
                "scripts/probe_portal.py and set the site override in the "
                "integration options."
            )
        return await self._fetch_page(path, "Inicio")

    async def fetch_consumos_page(self) -> str:
        """Fetch the consumption page to scrape a fresh `p_auth` token."""
        path = self._profile.consumos_path
        if path is None:
            raise VeoliaError(f"Don't know the consumos path for {self._base_url}")
        return await self._fetch_page(path, "Consumos")

    async def fetch_caudales(
        self, p_auth: str, fecha_inicio, fecha_fin, inicio: int = 0, fin: int = 199,
    ) -> dict:
        """Daily flow-rate telemetry (smart meters only)."""
        return await self._call_consumos_op(
            "buscarCaudales", p_auth, fecha_inicio, fecha_fin, inicio, fin, method="POST",
        )

    async def fetch_buscar_consumos(
        self, p_auth: str, fecha_inicio, fecha_fin, *,
        tipo: str = "periodo", inicio: int = 0, fin: int = 199,
    ) -> dict:
        """Family endpoint: `periodo` / `diaria` / `horaria` / `mensual`."""
        op = {
            "periodo": "buscarConsumos",
            "diaria": "buscarConsumosDiaria",
            "horaria": "buscarConsumosHoraria",
            "mensual": "buscarConsumosMensual",
        }.get(tipo, "buscarConsumos")
        return await self._call_consumos_op(
            op, p_auth, fecha_inicio, fecha_fin, inicio, fin, method="GET",
        )

    async def probe_login_page(self) -> Optional[str]:
        """Return the p_auth token from /login, or None if blocked/missing.

        Useful as a no-credentials CDN smoke test.
        """
        try:
            status, _, html = await self._get(
                f"{self._base_url}{self._profile.login_path}"
            )
        except CDNBlockedError:
            return None
        if status != 200:
            return None
        return self.extract_auth_token(html)

    async def _fetch_page(self, path: str, label: str) -> str:
        status, final_url, html = await self._get(f"{self._base_url}{path}")
        if status != 200:
            raise VeoliaError(f"{label} page returned HTTP {status}")
        if self._bounced_to_login(final_url, html):
            raise SessionExpiredError(f"{label} redirected back to login")
        return html

    def _bounced_to_login(self, final_url: str, html: str) -> bool:
        """True when a page fetch was redirected back to the login form."""
        return self._is_login_url(final_url) and bool(_LOGIN_FORM_RE.search(html))

    async def _call_consumos_op(
        self, op: str, p_auth: str, fecha_inicio, fecha_fin,
        inicio: int, fin: int, *, method: str,
    ) -> dict:
        if hasattr(fecha_inicio, "strftime"):
            fecha_inicio = fecha_inicio.strftime("%d/%m/%Y")
        if hasattr(fecha_fin, "strftime"):
            fecha_fin = fecha_fin.strftime("%d/%m/%Y")
        portlet = self._profile.consumos_portlet
        path = self._profile.consumos_path
        if path is None:
            raise VeoliaError(f"Don't know the consumos path for {self._base_url}")
        qs = {
            "p_p_id": portlet,
            "p_p_lifecycle": "2",
            "p_p_state": "normal",
            "p_p_mode": "view",
            "p_p_cacheability": "cacheLevelPage",
            "p_auth": p_auth,
            f"_{portlet}_op": op,
        }
        form = {
            f"_{portlet}_fechaInicio": fecha_inicio,
            f"_{portlet}_fechaFin": fecha_fin,
            f"_{portlet}_inicio": str(inicio),
            f"_{portlet}_fin": str(fin),
        }
        if method == "GET":
            url = f"{self._base_url}{path}?" + urlencode({**qs, **form})
            status, _, body = await self._get(url)
        else:
            url = f"{self._base_url}{path}?" + urlencode(qs)
            status, _, body = await self._post(url, form)
        if status == 401:
            raise SessionExpiredError(f"{op} returned 401")
        if status != 200:
            raise VeoliaError(f"{op} returned HTTP {status}")
        if not body:
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise VeoliaError(f"{op} returned non-JSON body: {e}") from e

    async def _get(self, url: str) -> tuple[int, str, str]:
        assert self._session is not None
        async with self._session.get(url) as resp:
            if resp.status in (403, 429):
                raise CDNBlockedError(f"GET {url} → {resp.status}")
            text = await resp.text(errors="replace")
            return resp.status, str(resp.url), text

    async def _post(self, url: str, data: dict) -> tuple[int, str, str]:
        assert self._session is not None
        async with self._session.post(url, data=data) as resp:
            if resp.status in (403, 429):
                raise CDNBlockedError(f"POST {url} → {resp.status}")
            text = await resp.text(errors="replace")
            return resp.status, str(resp.url), text
