"""Portal-specific endpoints and request shapes.

The defaults target Veolia / Sorea Catalonia (sorea.veolia.cat). The portal
runs Liferay DXP, so most of these knobs are Liferay portlet identifiers and
path prefixes. They sit in one module so a future adapter for another Veolia
portal can override them without touching the client logic.
"""
from __future__ import annotations

DEFAULT_BASE_URL = "https://sorea.veolia.cat"

LOGIN_PATH = "/login"
INICIO_PATH = "/group/soreaonline/inicio"
CONSUMOS_PATH = "/group/soreaonline/mis-consumos"

LOGIN_PORTLET = "CustomLoginPortlet"
CONSUMOS_PORTLET = "MisConsumos"

# Substring that must be present in the final URL of a successful login redirect.
LOGGED_IN_URL_MARKER = "soreaonline"

# Realistic recent-Chromium fingerprint. Imperva sits in front of the portal
# and returns 403 to obvious automation defaults.
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
