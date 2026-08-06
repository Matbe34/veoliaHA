import pytest

from custom_components.veolia_water.portal import (
    apply_overrides,
    build_headers,
    profile_for_url,
    site_from_url,
)
from custom_components.veolia_water.veolia_client import VeoliaClient

LOGIN_HTML = '<form><input name="_CustomLoginPortlet_password" type="password"></form>'
INICIO_HTML = '<script>var x = {"contrato":{"number":"1"}};</script>'


# ----- profile selection ---------------------------------------------------
def test_sorea_profile_matches_the_previously_hardcoded_paths():
    """Locks in that the refactor didn't move Sorea's endpoints."""
    p = profile_for_url("https://sorea.veolia.cat")
    assert p.key == "sorea" and p.confirmed
    assert p.login_path == "/login"
    assert p.inicio_path == "/group/soreaonline/inicio"
    assert p.consumos_path == "/group/soreaonline/mis-consumos"
    assert p.login_portlet == "CustomLoginPortlet"
    assert p.consumos_portlet == "MisConsumos"


@pytest.mark.parametrize("url,key", [
    ("https://sorea.veolia.cat", "sorea"),
    ("https://agbar.veolia.cat", "agbar"),
    ("https://hidrogea.veolia.es", "hidrogea"),
    ("https://www.agbar.veolia.cat/", "agbar"),
    ("https://something.else.example", "generic"),
])
def test_profile_for_url_selects_by_hostname(url, key):
    assert profile_for_url(url).key == key


def test_agbar_profile_matches_the_probe_output_from_issue_3():
    p = profile_for_url("https://agbar.veolia.cat")
    assert p.confirmed
    # Site stays unset so login sends no `redirect`, matching the probed flow.
    assert p.site is None
    discovered = p.with_site("sgab")
    assert discovered.inicio_path == "/group/sgab/inicio"
    assert discovered.consumos_path == "/group/sgab/mis-consumos"
    assert discovered.consumos_portlet == "MisConsumos"


def test_profiles_without_a_site_leave_it_to_discovery():
    p = profile_for_url("https://hidrogea.veolia.es")
    assert p.confirmed is False
    assert p.site is None
    assert p.inicio_path is None


def test_generic_profile_keeps_the_given_base_url():
    p = profile_for_url("https://water.example.com/")
    assert p.base_url == "https://water.example.com"
    assert p.site is None


def test_hostname_prefix_is_not_chewed_by_character_stripping():
    # "wwwater" must not lose leading characters to a "www." strip.
    assert profile_for_url("https://wwwater.example.com").base_url.endswith(
        "wwwater.example.com"
    )


# ----- site discovery ------------------------------------------------------
@pytest.mark.parametrize("url,expected", [
    ("https://x.cat/group/soreaonline/inicio", "soreaonline"),
    ("https://x.cat/group/agbaronline/inicio?p_auth=abc", "agbaronline"),
    ("https://x.cat/group/site-name/mis-consumos", "site-name"),
    ("https://x.cat/login", None),
    ("", None),
])
def test_site_from_url(url, expected):
    assert site_from_url(url) == expected


def test_with_site_fills_in_the_paths():
    p = profile_for_url("https://hidrogea.veolia.es").with_site("hidrogeaonline")
    assert p.inicio_path == "/group/hidrogeaonline/inicio"
    assert p.consumos_path == "/group/hidrogeaonline/mis-consumos"


# ----- overrides -----------------------------------------------------------
def test_apply_overrides_ignores_blanks():
    p = profile_for_url("https://sorea.veolia.cat")
    assert apply_overrides(p, {}) is p
    assert apply_overrides(p, {"site": ""}) is p
    assert apply_overrides(p, {"site": None}) is p


@pytest.mark.parametrize("value", [
    "sgab",
    " sgab ",
    "/group/sgab/inicio",
    "group/sgab",
    "https://agbar.veolia.cat/ca/group/sgab/inicio",
])
def test_site_override_accepts_a_pasted_path_or_url(value):
    """Issue #3: pasting the whole path built /group//group/sgab/inicio/inicio."""
    p = apply_overrides(profile_for_url("https://agbar.veolia.cat"), {"site": value})
    assert p.site == "sgab"
    assert p.inicio_path == "/group/sgab/inicio"


def test_apply_overrides_sets_site_and_portlets():
    p = apply_overrides(
        profile_for_url("https://hidrogea.veolia.es"),
        {"site": " hidrogeaonline ", "consumos_portlet": "MisConsumosV2"},
    )
    assert p.inicio_path == "/group/hidrogeaonline/inicio"
    assert p.consumos_portlet == "MisConsumosV2"


def test_build_headers_uses_the_profile_language():
    assert build_headers(profile_for_url("https://sorea.veolia.cat"))[
        "Accept-Language"
    ].startswith("ca")
    assert build_headers(profile_for_url("https://hidrogea.veolia.es"))[
        "Accept-Language"
    ].startswith("es")


# ----- tenant-independent session checks -----------------------------------
def _client(url="https://hidrogea.veolia.es"):
    return VeoliaClient("u", "p", base_url=url)


def test_bounced_to_login_needs_both_the_path_and_a_password_form():
    c = _client()
    assert c._bounced_to_login("https://x.es/login", LOGIN_HTML) is True
    # A tenant whose site name happens to contain "login" must not trip this.
    assert c._bounced_to_login("https://x.es/group/loginonline/inicio", INICIO_HTML) is False
    assert c._bounced_to_login("https://x.es/login", INICIO_HTML) is False


def test_is_authenticated_accepts_an_auth_cookie(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "_has_auth_cookie", lambda: True)
    assert c._is_authenticated("https://x.es/login", LOGIN_HTML) is True


def test_is_authenticated_falls_back_to_absence_of_a_login_form(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "_has_auth_cookie", lambda: False)
    # No cookie, but we landed on a real page — treat as success. This is the
    # case that used to fail on every non-Sorea portal.
    assert c._is_authenticated("https://x.es/group/whatever/inicio", INICIO_HTML) is True
    assert c._is_authenticated("https://x.es/login", LOGIN_HTML) is False
    # A site name that merely starts with "login" is not the login page.
    assert c._is_authenticated("https://x.es/group/loginonline/inicio", INICIO_HTML) is True
