"""HTTP client for the Veolia / Sorea Liferay portal.

Lives outside HA's shared aiohttp session because we need full control of
cookies and headers — the portal sits behind Imperva and is sensitive to
shared/leaky state.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, timedelta
from typing import Optional
from urllib.parse import urlencode, urlsplit

import aiohttp

from .const import (
    CONSUMOS_PAGE_SIZE,
    DAILY_WINDOW_DAYS,
    HOURLY_WINDOW_DAYS,
    MAX_CONSUMOS_PAGES,
    MONTHLY_WINDOW_MONTHS,
)
from .models import Contract
from .parser import ParseError, parse_contract_list
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

LOGIN_ATTEMPTS = 5
LOGIN_RETRY_DELAY = 3.0


def month_start(d: date, months_back: int = 0) -> date:
    """The 1st of the month `months_back` months before `d`'s month."""
    index = d.year * 12 + d.month - 1 - months_back
    return date(index // 12, index % 12 + 1, 1)


class VeoliaError(Exception):
    """Base error for any non-recoverable portal failure."""


class LoginError(VeoliaError):
    """Authentication was rejected — credentials wrong or portal markup changed."""


class UnsupportedPortalError(VeoliaError):
    """The URL serves no Liferay login page: a different platform entirely."""


class CDNBlockedError(VeoliaError):
    """The CDN in front of the portal returned 403 or 429."""


class SessionExpiredError(VeoliaError):
    """A request landed back on /login mid-cycle."""


class PortalHTTPError(VeoliaError):
    """A portal endpoint answered with an unexpected HTTP status."""

    def __init__(self, op: str, status: int) -> None:
        super().__init__(f"{op} returned HTTP {status}")
        self.status = status


# Safety stop for the contract list's paging loop.
_MAX_CONTRACT_PAGES = 20

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
        contract: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self._username = username
        self._password = password
        # Sent as the login form's `lastContract`. Blank lets the portal pick.
        self._contract = (contract or "").strip()
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
        # The portal sometimes answers a valid login with the login page again
        # (a session cookie is set but nothing is authorised). A fresh attempt
        # works, so retry before giving up. Never retry a rejected password.
        for attempt in range(1, LOGIN_ATTEMPTS + 1):
            if await self._login_once():
                return
            _LOGGER.debug("Login bounced back to the login page (attempt %d)", attempt)
            if attempt < LOGIN_ATTEMPTS:
                if self._session is not None:
                    self._session.cookie_jar.clear()
                await asyncio.sleep(LOGIN_RETRY_DELAY)
        raise VeoliaError(
            f"{self._base_url} kept returning the login page after "
            f"{LOGIN_ATTEMPTS} attempts; try again later"
        )

    async def _login_once(self) -> bool:
        """One login try; False if the portal bounced us back to the login page."""
        profile = self._profile
        # Only a rejected password may raise LoginError: the config flow shows
        # it as "wrong username or password", which on another platform's
        # site (issue #9) sends the user off resetting a working password.
        status, _, html = await self._get(f"{self._base_url}{profile.login_path}")
        if status == 404:
            raise UnsupportedPortalError(
                f"{self._base_url} has no {profile.login_path} page (HTTP 404)"
            )
        if status != 200:
            raise VeoliaError(f"Login page returned HTTP {status}")
        p_auth = self.extract_auth_token(html)
        if not p_auth:
            raise UnsupportedPortalError("Could not find p_auth token on login page")

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
            # Only a hint: the portal may ignore it, so the coordinator also
            # calls select_contract() when the wrong contract comes back.
            f"_{portlet}_lastContract": self._contract,
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
            raise VeoliaError(f"Login POST returned HTTP {status}")
        if not self._is_authenticated(final_url, body):
            raise LoginError(f"Authentication rejected (landed on {final_url})")
        if self._is_login_url(final_url):
            return False

        self._landing_html = body
        learned = site_from_url(final_url)
        if learned and learned != profile.site:
            _LOGGER.debug("Learned Liferay site '%s' from %s", learned, final_url)
            self._profile = profile.with_site(learned)
        _LOGGER.info("Authenticated against %s", self._base_url)
        return True

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

    async def _contracts_page(self) -> tuple[str, str]:
        """Path of the contracts page and the `p_auth` token it carries."""
        path = self._profile.contracts_path
        if path is None:
            raise VeoliaError(f"Don't know the contracts path for {self._base_url}")
        p_auth = self.extract_auth_token(await self._fetch_page(path, "Contracts"))
        if not p_auth:
            raise VeoliaError("No p_auth token on the contracts page")
        return path, p_auth

    async def list_contracts(self, page_size: int = 50) -> list[Contract]:
        """Every contract on the account, from the portal's contract picker.

        The picker loads them a page at a time (`loadContratos`), so keep
        asking until the account's total is reached.
        """
        path, p_auth = await self._contracts_page()
        ns = f"_{self._profile.contracts_portlet}_"
        contracts: list[Contract] = []
        for _ in range(_MAX_CONTRACT_PAGES):
            qs = urlencode({
                "p_p_id": self._profile.contracts_portlet,
                "p_p_lifecycle": "2",
                "p_p_state": "normal",
                "p_p_mode": "view",
                "p_p_cacheability": "cacheLevelPage",
                f"{ns}op": "loadContratos",
                f"{ns}offset": str(len(contracts)),
                f"{ns}limit": str(page_size),
                f"{ns}contractNumber": "",
                f"{ns}identityDocument": "",
                f"{ns}supplyAddress": "",
                "p_auth": p_auth,
            })
            status, final_url, body = await self._get(f"{self._base_url}{path}?{qs}")
            if status != 200:
                raise VeoliaError(f"Contract list returned HTTP {status}")
            if self._bounced_to_login(final_url, body):
                raise SessionExpiredError("Contract list redirected back to login")
            try:
                page, total = parse_contract_list(json.loads(body))
            except (json.JSONDecodeError, ParseError) as e:
                raise VeoliaError(f"Unreadable contract list: {e}") from e
            contracts.extend(page)
            if not page or len(contracts) >= total:
                break
        return contracts

    async def select_contract(self, number: str) -> None:
        """Make `number` the session's contract, as the portal's own picker does.

        The portal serves one contract at a time. Its contract list links each
        row to this `seleccionarContrato` action; every page fetched afterwards
        shows the chosen contract.
        """
        path, p_auth = await self._contracts_page()
        portlet = self._profile.contracts_portlet
        qs = urlencode({
            "p_p_id": portlet,
            "p_p_lifecycle": "1",
            "p_p_state": "normal",
            "p_p_mode": "view",
            f"_{portlet}_javax.portlet.action": "seleccionarContrato",
            "p_auth": p_auth,
            f"_{portlet}_numeroContrato": number,
        })
        status, final_url, html = await self._get(f"{self._base_url}{path}?{qs}")
        if status != 200:
            raise VeoliaError(f"Selecting a contract returned HTTP {status}")
        if self._bounced_to_login(final_url, html):
            raise SessionExpiredError("Selecting a contract redirected back to login")

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

    async def fetch_consumos_range(
        self, p_auth: str, fecha_inicio: date, fecha_fin: date, *, tipo: str,
        keep_partial: bool = True,
    ) -> dict:
        """Every row of `fecha_inicio..fecha_fin` for `tipo` (`horaria`/`diaria`/`mensual`).

        The portal caps a query at ~1000 rows and returns nothing for windows
        that are too long, so the range is cut into windows (walked newest to
        oldest) and each one is paged. Monthly windows hold whole calendar
        months, so no month is split across two queries. Stops after two empty
        windows in a row, i.e. before the meter existed. Returns the same shape as one response
        (`{"consumos": [...]}`, newest first).

        A window that fails after some rows were already read ends the walk
        with what we have rather than discarding it, unless `keep_partial` is
        False: then any failure raises, so an incomplete backfill isn't stored.
        """
        window = HOURLY_WINDOW_DAYS if tipo == "horaria" else DAILY_WINDOW_DAYS
        if tipo == "mensual":
            fecha_inicio = month_start(fecha_inicio)
        consumos: list = []
        end = fecha_fin
        empty_in_a_row = 0
        while end >= fecha_inicio and empty_in_a_row < 2:
            if tipo == "mensual":
                start = max(fecha_inicio, month_start(end, MONTHLY_WINDOW_MONTHS - 1))
            else:
                start = max(fecha_inicio, end - timedelta(days=window - 1))
            try:
                rows = await self._fetch_window(p_auth, start, end, tipo)
            except (SessionExpiredError, CDNBlockedError):
                raise
            except VeoliaError as e:
                if not consumos or not keep_partial:
                    raise
                _LOGGER.warning("%s %s..%s failed (%s); keeping what was read.", tipo, start, end, e)
                break
            empty_in_a_row = 0 if rows else empty_in_a_row + 1
            consumos.extend(rows)
            end = start - timedelta(days=1)
        return {"consumos": consumos}

    async def _fetch_window(self, p_auth: str, start: date, end: date, tipo: str) -> list:
        rows: list = []
        for number in range(MAX_CONSUMOS_PAGES):
            payload = await self._fetch_page_retrying(
                p_auth, start, end, tipo, number * CONSUMOS_PAGE_SIZE,
            )
            page = payload.get("consumos") or [] if isinstance(payload, dict) else []
            rows.extend(page)
            if not page or payload.get("ultimaPagina", True):
                return rows
        _LOGGER.warning(
            "%s %s..%s still had more pages after %d; keeping those.",
            tipo, start, end, MAX_CONSUMOS_PAGES,
        )
        return rows

    async def _fetch_page_retrying(self, p_auth, start, end, tipo, first) -> dict:
        """One page, retrying the portal's transient 5xx (it throws 504s now and then)."""
        async def fetch() -> dict:
            # `fin` is inclusive.
            return await self.fetch_buscar_consumos(
                p_auth, start, end, tipo=tipo,
                inicio=first, fin=first + CONSUMOS_PAGE_SIZE - 1,
            )

        for attempt in (1, 2):
            try:
                return await fetch()
            except PortalHTTPError as e:
                if not 500 <= e.status < 600:
                    raise
            await asyncio.sleep(2 * attempt)
        return await fetch()

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
            raise PortalHTTPError(op, status)
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
