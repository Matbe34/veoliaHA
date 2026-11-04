"""The per-poll contract loop: one login, then each configured contract in turn."""
import asyncio
import json

import pytest

from custom_components.veolia_water.cycle import (
    ContractMismatchError,
    configured_contracts,
    run_cycle,
)
from custom_components.veolia_water.veolia_client import (
    CDNBlockedError,
    LoginError,
    VeoliaError,
)


def _inicio(number: str, smart: bool = False) -> str:
    blob = {"contrato": {"number": number, "smartMetering": smart, "supplyAddress": f"Street {number}"}}
    return f"<script>var x = {json.dumps(blob)};</script>"


class FakePortal:
    """A logged-in session that shows one contract at a time, like the real portal."""

    def __init__(self, opens_on="A", *, smart=False, ignore=(), fail=(), fail_with=VeoliaError):
        self.shown = opens_on
        self.smart = smart
        self.ignore = set(ignore)      # select requests the portal silently ignores
        self.fail = set(fail)          # select requests that error out
        self.fail_with = fail_with
        self.log: list[tuple[str, str]] = []

    async def login(self):
        self.log.append(("login", self.shown))

    async def fetch_inicio(self):
        self.log.append(("inicio", self.shown))
        return _inicio(self.shown, self.smart)

    async def select_contract(self, number):
        self.log.append(("select", number))
        if number in self.fail:
            raise self.fail_with(f"select {number} failed")
        if number not in self.ignore:
            self.shown = number

    async def fetch_consumos_page(self):
        self.log.append(("consumos", self.shown))
        return "<html>no token</html>"  # meter fetch stops quietly here

    @staticmethod
    def extract_auth_token(html):
        return None

    def calls(self, kind):
        return [value for k, value in self.log if k == kind]


def _run(portal, wanted):
    return asyncio.run(run_cycle(portal, wanted))


# ----- which contracts to read ---------------------------------------------
@pytest.mark.parametrize("opts,expected", [
    ({"contracts": ["A", "B"]}, ["A", "B"]),
    ({"contracts": [" A ", "A", "", "B"]}, ["A", "B"]),
    # Set up before the picker existed: one number, or none.
    ({"contract_number": "A"}, ["A"]),
    ({"contract_number": " "}, []),
    ({}, []),
    # A saved list wins over the old field, even when empty.
    ({"contracts": [], "contract_number": "A"}, []),
    ({"contracts": ["B"], "contract_number": "A"}, ["B"]),
])
def test_configured_contracts(opts, expected):
    assert configured_contracts(opts) == expected


# ----- the loop --------------------------------------------------------------
def test_with_nothing_configured_it_reads_the_contract_the_portal_opens_on():
    portal = FakePortal(opens_on="A")
    result = _run(portal, [])
    assert list(result.snapshots) == ["A"]
    assert portal.calls("select") == []


def test_reads_every_configured_contract_in_one_login():
    """Issue #10's account: two contracts, the portal opens on the unwanted one."""
    portal = FakePortal(opens_on="A")
    result = _run(portal, ["A", "B"])
    assert portal.calls("login") == ["A"]
    assert portal.calls("select") == ["B"]
    assert set(result.snapshots) == {"A", "B"}
    assert result.snapshots["B"].contract.contract_number == "B"
    assert result.snapshots["B"].contract.address == "Street B"
    assert result.errors == {}


def test_starts_with_the_contract_the_portal_opened_on():
    """Saves a switch: no select for the one already shown."""
    portal = FakePortal(opens_on="A")
    _run(portal, ["B", "A"])
    assert portal.calls("select") == ["B"]


def test_a_single_configured_contract_switches_once():
    portal = FakePortal(opens_on="A")
    result = _run(portal, ["B"])
    assert portal.calls("select") == ["B"]
    assert list(result.snapshots) == ["B"]


def test_meter_data_is_fetched_after_the_switch():
    """The meter endpoints follow the session's contract, so order matters."""
    portal = FakePortal(opens_on="A", smart=True)
    _run(portal, ["A", "B"])
    assert portal.calls("consumos") == ["A", "B"]
    assert portal.log.index(("select", "B")) < portal.log.index(("consumos", "B"))


def test_a_switch_the_portal_ignores_is_an_error_not_wrong_data():
    portal = FakePortal(opens_on="A", ignore={"B"})
    result = _run(portal, ["A", "B"])
    assert list(result.snapshots) == ["A"]
    assert isinstance(result.errors["B"], ContractMismatchError)


def test_one_failed_contract_does_not_stop_the_others():
    portal = FakePortal(opens_on="A", fail={"B"})
    result = _run(portal, ["A", "B", "C"])
    assert set(result.snapshots) == {"A", "C"}
    assert set(result.errors) == {"B"}
    # After the failure the session's contract is unknown, so C is selected explicitly.
    assert portal.calls("select") == ["B", "C"]


def test_all_contracts_failing_returns_no_snapshots():
    portal = FakePortal(opens_on="A", fail={"B", "C"})
    result = _run(portal, ["B", "C"])
    assert result.snapshots == {}
    assert set(result.errors) == {"B", "C"}


def test_a_cdn_block_mid_cycle_ends_the_cycle():
    portal = FakePortal(opens_on="A", fail={"B"}, fail_with=CDNBlockedError)
    with pytest.raises(CDNBlockedError):
        _run(portal, ["A", "B"])


def test_a_rejected_login_ends_the_cycle():
    class Rejecting(FakePortal):
        async def login(self):
            raise LoginError("nope")

    with pytest.raises(LoginError):
        _run(Rejecting(), ["A"])
