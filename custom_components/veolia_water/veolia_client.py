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
from urllib.parse import urlencode

import aiohttp

from .portal import (
    CONSUMOS_PATH,
    CONSUMOS_PORTLET,
    DEFAULT_BASE_URL,
    DEFAULT_HEADERS,
    INICIO_PATH,
    LOGGED_IN_URL_MARKER,
    LOGIN_PATH,
    LOGIN_PORTLET,
)

_LOGGER = logging.getLogger(__name__)


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
        timeout: float = 30.0,
    ) -> None:
        self._username = username
        self._password = password
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self) -> "VeoliaClient":
        self._session = aiohttp.ClientSession(
            headers=DEFAULT_HEADERS,
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
        status, _, html = await self._get(f"{self._base_url}{LOGIN_PATH}")
        if status != 200:
            raise LoginError(f"Login page returned HTTP {status}")
        p_auth = self.extract_auth_token(html)
        if not p_auth:
            raise LoginError("Could not find p_auth token on login page")

        action_qs = urlencode({
            "p_p_id": LOGIN_PORTLET,
            "p_p_lifecycle": "1",
            "p_p_state": "normal",
            "p_p_mode": "view",
            f"_{LOGIN_PORTLET}_javax.portlet.action": "/login/login",
            f"_{LOGIN_PORTLET}_mvcRenderCommandName": "/login/login",
            "p_auth": p_auth,
        })
        form = {
            f"_{LOGIN_PORTLET}_login": self._username,
            f"_{LOGIN_PORTLET}_password": self._password,
            f"_{LOGIN_PORTLET}_lastContract": "",
            "redirect": INICIO_PATH,
            "doActionAfterLogin": "false",
            "saveLastPath": "false",
            "idiomasExcluidosId": "",
        }
        status, final_url, _ = await self._post(
            f"{self._base_url}{LOGIN_PATH}?{action_qs}", form
        )
        if "/login" in final_url and LOGGED_IN_URL_MARKER not in final_url:
            raise LoginError(f"Authentication rejected (landed on {final_url})")
        if status >= 400:
            raise LoginError(f"Login POST returned HTTP {status}")
        _LOGGER.info("Authenticated against %s", self._base_url)

    async def fetch_inicio(self) -> str:
        return await self._fetch_page(INICIO_PATH, "Inicio")

    async def fetch_consumos_page(self) -> str:
        """Fetch the consumption page to scrape a fresh `p_auth` token."""
        return await self._fetch_page(CONSUMOS_PATH, "Consumos")

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
            status, _, html = await self._get(f"{self._base_url}{LOGIN_PATH}")
        except CDNBlockedError:
            return None
        if status != 200:
            return None
        return self.extract_auth_token(html)

    async def _fetch_page(self, path: str, label: str) -> str:
        status, final_url, html = await self._get(f"{self._base_url}{path}")
        if status != 200:
            raise VeoliaError(f"{label} page returned HTTP {status}")
        if "/login" in final_url and LOGGED_IN_URL_MARKER not in final_url:
            raise SessionExpiredError(f"{label} redirected back to login")
        return html

    async def _call_consumos_op(
        self, op: str, p_auth: str, fecha_inicio, fecha_fin,
        inicio: int, fin: int, *, method: str,
    ) -> dict:
        if hasattr(fecha_inicio, "strftime"):
            fecha_inicio = fecha_inicio.strftime("%d/%m/%Y")
        if hasattr(fecha_fin, "strftime"):
            fecha_fin = fecha_fin.strftime("%d/%m/%Y")
        qs = {
            "p_p_id": CONSUMOS_PORTLET,
            "p_p_lifecycle": "2",
            "p_p_state": "normal",
            "p_p_mode": "view",
            "p_p_cacheability": "cacheLevelPage",
            "p_auth": p_auth,
            f"_{CONSUMOS_PORTLET}_op": op,
        }
        form = {
            f"_{CONSUMOS_PORTLET}_fechaInicio": fecha_inicio,
            f"_{CONSUMOS_PORTLET}_fechaFin": fecha_fin,
            f"_{CONSUMOS_PORTLET}_inicio": str(inicio),
            f"_{CONSUMOS_PORTLET}_fin": str(fin),
        }
        if method == "GET":
            url = f"{self._base_url}{CONSUMOS_PATH}?" + urlencode({**qs, **form})
            status, _, body = await self._get(url)
        else:
            url = f"{self._base_url}{CONSUMOS_PATH}?" + urlencode(qs)
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
