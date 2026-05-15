"""`vpnpilot catalog dump` subcommand integration tests. All subprocess mocked."""

from __future__ import annotations

import json

import pytest
from PyQt6.QtCore import QCoreApplication

from vpnpilot.catalog._cli import _dump
from vpnpilot.cli import CLIResult, ProtonCLI

COUNTRIES_STDOUT = """\
Country                           Code
--------------------------------  ------
Iceland                           IS
United States                     US
"""

CITIES_IS_STDOUT = """\
Cities in Iceland:
City       Features
---------  ----------
Reykjavik  P2P
"""

CITIES_US_STDOUT = """\
Cities in United States:
City            Features
--------------  ----------
Seattle         P2P
Atlanta         P2P, Tor
"""

AUTH_ERR_STDERR = (
    "Error: Authentication required to view complete country list. "
    "Please sign in with 'protonvpn signin'"
)

CITIES_ERR_STDERR = "Error: Invalid country code 'IS'. Please use a valid country code."


@pytest.fixture
def qapp():
    return QCoreApplication.instance() or QCoreApplication([])


class ScriptedCLI(ProtonCLI):
    def __init__(self, countries_result: CLIResult, cities_results: dict[str, CLIResult]) -> None:
        self._countries_result = countries_result
        self._cities_results = cities_results

    async def countries_list(self) -> CLIResult:  # type: ignore[override]
        return self._countries_result

    async def cities_list(self, country_code: str) -> CLIResult:  # type: ignore[override]
        return self._cities_results.get(country_code, CLIResult(0, "", ""))


@pytest.mark.asyncio
async def test_dump_json_shape(qapp, capsys):
    cli = ScriptedCLI(
        CLIResult(0, COUNTRIES_STDOUT, ""),
        {
            "IS": CLIResult(0, CITIES_IS_STDOUT, ""),
            "US": CLIResult(0, CITIES_US_STDOUT, ""),
        },
    )
    rc = await _dump(cli)
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "countries" in data
    by_code = {c["code"]: c for c in data["countries"]}
    assert set(by_code.keys()) == {"IS", "US"}
    assert by_code["IS"]["name"] == "Iceland"
    assert by_code["IS"]["cities"] == [{"name": "Reykjavik", "features": ["P2P"]}]
    seattle = next(c for c in by_code["US"]["cities"] if c["name"] == "Seattle")
    assert seattle["features"] == ["P2P"]
    atlanta = next(c for c in by_code["US"]["cities"] if c["name"] == "Atlanta")
    assert set(atlanta["features"]) == {"P2P", "Tor"}


@pytest.mark.asyncio
async def test_dump_exits_1_on_auth_failure(qapp, capsys):
    cli = ScriptedCLI(CLIResult(2, "", AUTH_ERR_STDERR), {})
    rc = await _dump(cli)
    assert rc == 1
    err = capsys.readouterr().err
    assert "sign in" in err.lower() or "signed in" in err.lower()


@pytest.mark.asyncio
async def test_dump_exits_1_on_partial_city_failure(qapp, capsys):
    cli = ScriptedCLI(
        CLIResult(0, COUNTRIES_STDOUT, ""),
        {
            "IS": CLIResult(2, "", CITIES_ERR_STDERR),
            "US": CLIResult(0, CITIES_US_STDOUT, ""),
        },
    )
    rc = await _dump(cli)
    assert rc == 1
    out = capsys.readouterr().out
    data = json.loads(out)
    # US should still appear
    codes = [c["code"] for c in data["countries"]]
    assert "US" in codes
    assert "IS" not in codes


@pytest.mark.asyncio
async def test_dump_features_sorted(qapp, capsys):
    cli = ScriptedCLI(
        CLIResult(0, COUNTRIES_STDOUT, ""),
        {
            "IS": CLIResult(0, CITIES_IS_STDOUT, ""),
            "US": CLIResult(0, CITIES_US_STDOUT, ""),
        },
    )
    await _dump(cli)
    out = capsys.readouterr().out
    data = json.loads(out)
    for country in data["countries"]:
        for city in country["cities"]:
            assert city["features"] == sorted(city["features"])
