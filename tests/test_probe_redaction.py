"""The probe's output gets pasted into public issues — nothing identifying
may survive `clean()`."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "probe_portal.py"


@pytest.fixture
def probe():
    spec = importlib.util.spec_from_file_location("probe_portal", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._secrets.clear()
    yield mod
    mod._secrets.clear()


def test_credentials_are_redacted(probe):
    probe._secrets.extend(["me@example.com", "hunter2", "me"])
    got = probe.clean("login=me@example.com password=hunter2")
    assert "hunter2" not in got
    assert "me@example.com" not in got


def test_unrelated_emails_are_redacted(probe):
    """Even an address we were never told about — the portal may echo one."""
    assert "someone.else@agbar.cat" not in probe.clean("owner: someone.else@agbar.cat")


@pytest.mark.parametrize("secret", [
    "9999999",          # contract number
    "12345678",         # DNI
    "F2026000001",      # invoice id
    "004512339",        # meter serial
])
def test_long_digit_runs_are_redacted(probe, secret):
    assert secret not in probe.clean(f"value={secret}")


def test_query_strings_are_stripped(probe):
    got = probe.clean("https://x.cat/inicio?p_auth=AbC123&contrato=9999999")
    assert "p_auth" not in got and "9999999" not in got


def test_reportable_details_survive_redaction(probe):
    """Redaction must not eat the diagnostics the probe exists to collect."""
    kept = probe.clean(
        "/group/sgab/mis-consumos MisConsumos CustomLoginPortlet "
        "keys = ['anyo', 'consumo', 'lecturaEstimada'] 2026 10 records"
    )
    for token in ("/group/sgab/mis-consumos", "MisConsumos", "CustomLoginPortlet",
                  "lecturaEstimada", "2026", "10 records"):
        assert token in kept


def test_link_scan_does_not_swallow_surrounding_page_text(probe, capsys):
    """The path regex must stop at the href, not run on into page content."""
    html = (
        '<a href="/group/sgab/mis-consumos">x</a>\n'
        '<!-- /group/sgab/inicio\n'
        'Owner: Jane Roe, CARRER EXEMPLE 1, meter 004512339 -->'
    )
    probe.describe_structure(html, "landing")
    printed = capsys.readouterr().out
    assert "/group/sgab/mis-consumos" in printed
    for leaked in ("Jane Roe", "CARRER EXEMPLE", "Owner"):
        assert leaked not in printed


def test_every_output_helper_routes_through_clean(probe, capsys):
    probe._secrets.append("hunter2")
    probe.say("token hunter2")
    probe.out("label", "contract 9999999 hunter2")
    printed = capsys.readouterr().out
    assert "hunter2" not in printed
    assert "9999999" not in printed
    assert "label" in printed
