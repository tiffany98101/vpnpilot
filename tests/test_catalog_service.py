"""ServerCatalog service tests. All subprocess calls mocked."""

from __future__ import annotations

import asyncio

import pytest
from PyQt6.QtCore import QCoreApplication

from vpnpilot.catalog.models import CatalogError, CityFeature, EntryState
from vpnpilot.catalog.service import ServerCatalog
from vpnpilot.cli import CLIResult, ProtonCLI

# ---- fixtures -------------------------------------------------------

COUNTRIES_STDOUT = """\
Country                           Code
--------------------------------  ------
Iceland                           IS
United States                     US
Brazil                            BR
"""

CITIES_US_STDOUT = """\
Cities in United States:
City            Features
--------------  ----------
Seattle         P2P
Atlanta         P2P, Tor
Salt Lake City
"""

CITIES_IS_STDOUT = """\
Cities in Iceland:
City       Features
---------  ----------
Reykjavik  P2P
"""

CITIES_BR_STDOUT = """\
Cities in Brazil:
City       Features
---------  ----------
São Paulo  P2P
"""

AUTH_ERR_STDERR = (
    "Error: Authentication required to view complete country list. "
    "Please sign in with 'protonvpn signin'"
)

INVALID_CODE_STDERR = (
    "Error: Invalid country code 'ZZ'. Please use a valid country code.\n\n"
    "Try 'protonvpn cities list --help' for more information."
)


@pytest.fixture
def qapp():
    return QCoreApplication.instance() or QCoreApplication([])


class ScriptedCLI(ProtonCLI):
    """Returns pre-scripted results for countries_list and cities_list."""

    def __init__(
        self,
        countries_result: CLIResult,
        cities_results: dict[str, CLIResult] | None = None,
        *,
        cities_default: CLIResult | None = None,
    ) -> None:
        self._countries_result = countries_result
        self._cities_results: dict[str, CLIResult] = cities_results or {}
        self._cities_default = cities_default or CLIResult(0, "", "")
        self.countries_calls = 0
        self.cities_calls: list[str] = []

    async def countries_list(self) -> CLIResult:  # type: ignore[override]
        self.countries_calls += 1
        return self._countries_result

    async def cities_list(self, country_code: str) -> CLIResult:  # type: ignore[override]
        self.cities_calls.append(country_code)
        return self._cities_results.get(country_code, self._cities_default)


def make_cli(
    countries_ok: bool = True,
    cities_results: dict[str, CLIResult] | None = None,
) -> ScriptedCLI:
    cr = CLIResult(0, COUNTRIES_STDOUT, "") if countries_ok else CLIResult(2, "", AUTH_ERR_STDERR)
    default_cities = CLIResult(0, CITIES_US_STDOUT, "")
    return ScriptedCLI(cr, cities_results or {}, cities_default=default_cities)


# ---- countries() tests ----------------------------------------------

@pytest.mark.asyncio
async def test_countries_fetches_on_first_call(qapp):
    cli = make_cli()
    catalog = ServerCatalog(cli)
    result = await catalog.countries()
    assert [c.code for c in result] == ["IS", "US", "BR"]
    assert cli.countries_calls == 1


@pytest.mark.asyncio
async def test_countries_returns_cache_on_second_call(qapp):
    cli = make_cli()
    catalog = ServerCatalog(cli)
    await catalog.countries()
    await catalog.countries()
    assert cli.countries_calls == 1


@pytest.mark.asyncio
async def test_countries_raises_on_auth_failure(qapp):
    cli = make_cli(countries_ok=False)
    catalog = ServerCatalog(cli)
    with pytest.raises(CatalogError):
        await catalog.countries()


@pytest.mark.asyncio
async def test_concurrent_countries_calls_dedup(qapp):
    cli = make_cli()

    async def slow_countries():
        await asyncio.sleep(0.02)
        return CLIResult(0, COUNTRIES_STDOUT, "")

    cli.countries_list = slow_countries  # type: ignore[method-assign]
    cli.countries_calls = 0
    original = cli.countries_list

    call_count = 0

    async def counting_countries():
        nonlocal call_count
        call_count += 1
        return await original()

    cli.countries_list = counting_countries  # type: ignore[method-assign]

    catalog = ServerCatalog(cli)
    results = await asyncio.gather(catalog.countries(), catalog.countries(), catalog.countries())
    # All three calls should return the same result
    assert all(r == results[0] for r in results)
    # Only one subprocess call
    assert call_count == 1


# ---- cities() tests -------------------------------------------------

@pytest.mark.asyncio
async def test_cities_returns_loading_state_immediately(qapp):
    cli = ScriptedCLI(
        CLIResult(0, COUNTRIES_STDOUT, ""),
        {"US": CLIResult(0, CITIES_US_STDOUT, "")},
    )

    async def slow_cities(code):
        await asyncio.sleep(0.05)
        return CLIResult(0, CITIES_US_STDOUT, "")

    cli.cities_list = slow_cities  # type: ignore[method-assign]

    catalog = ServerCatalog(cli)
    entry = catalog.cities("US")
    assert entry.state is EntryState.LOADING


@pytest.mark.asyncio
async def test_cities_loaded_after_fetch_completes(qapp):
    cli = ScriptedCLI(
        CLIResult(0, COUNTRIES_STDOUT, ""),
        {"US": CLIResult(0, CITIES_US_STDOUT, "")},
    )
    catalog = ServerCatalog(cli)
    cities = await catalog.cities_async("US")
    names = [c.name for c in cities]
    assert "Seattle" in names
    assert "Atlanta" in names
    assert "Salt Lake City" in names


@pytest.mark.asyncio
async def test_cities_features_parsed(qapp):
    cli = ScriptedCLI(
        CLIResult(0, COUNTRIES_STDOUT, ""),
        {"US": CLIResult(0, CITIES_US_STDOUT, "")},
    )
    catalog = ServerCatalog(cli)
    cities = await catalog.cities_async("US")
    by_name = {c.name: c for c in cities}
    assert CityFeature.TOR in by_name["Atlanta"].features
    assert by_name["Salt Lake City"].features == frozenset()


@pytest.mark.asyncio
async def test_cities_deduplicates_concurrent_calls(qapp):
    call_count = 0

    async def counting_cities(code):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.02)
        return CLIResult(0, CITIES_US_STDOUT, "")

    cli = make_cli()
    cli.cities_list = counting_cities  # type: ignore[method-assign]
    catalog = ServerCatalog(cli)

    # Kick off three concurrent cities() + wait for all
    tasks = [asyncio.create_task(catalog.cities_async("US")) for _ in range(3)]
    results = await asyncio.gather(*tasks)
    assert all(r == results[0] for r in results)
    assert call_count == 1


@pytest.mark.asyncio
async def test_cities_failed_entry_on_cli_error(qapp):
    cli = ScriptedCLI(
        CLIResult(0, COUNTRIES_STDOUT, ""),
        {"ZZ": CLIResult(2, "", INVALID_CODE_STDERR)},
    )
    catalog = ServerCatalog(cli)
    with pytest.raises(CatalogError):
        await catalog.cities_async("ZZ")
    entry = catalog._entries["ZZ"]
    assert entry.state is EntryState.FAILED
    assert entry.last_error is not None


def test_cities_raises_for_malformed_code(qapp):
    catalog = ServerCatalog(make_cli())
    with pytest.raises(CatalogError, match="Invalid country code"):
        catalog.cities("US123")


def test_cities_raises_for_empty_code(qapp):
    catalog = ServerCatalog(make_cli())
    with pytest.raises(CatalogError):
        catalog.cities("")


# ---- refresh() test -------------------------------------------------

@pytest.mark.asyncio
async def test_refresh_clears_cache_and_refetches(qapp):
    cli = make_cli()
    catalog = ServerCatalog(cli)
    await catalog.countries()
    assert cli.countries_calls == 1

    catalog.refresh()
    await catalog.countries()
    assert cli.countries_calls == 2


@pytest.mark.asyncio
async def test_refresh_ignores_stale_in_flight_city_result(qapp):
    started = asyncio.Event()
    release = asyncio.Event()

    async def stale_city_result(_code):
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            # Simulate a subprocess wrapper that still returns a result
            # even after a cancellation request.
            await release.wait()
        return CLIResult(0, CITIES_US_STDOUT, "")

    cli = make_cli()
    cli.cities_list = stale_city_result  # type: ignore[method-assign]
    catalog = ServerCatalog(cli)
    catalog.cities("US")
    old_task = catalog._loading_tasks["US"]
    await started.wait()

    catalog.refresh()
    assert catalog.entry_state("US") is EntryState.NOT_FETCHED

    release.set()
    await old_task

    assert catalog.entry_state("US") is EntryState.NOT_FETCHED
    assert catalog.cities_if_loaded("US") is None


# ---- prewarm() test -------------------------------------------------

@pytest.mark.asyncio
async def test_prewarm_fetches_all_countries(qapp):
    cli = ScriptedCLI(
        CLIResult(0, COUNTRIES_STDOUT, ""),
        {
            "IS": CLIResult(0, CITIES_IS_STDOUT, ""),
            "US": CLIResult(0, CITIES_US_STDOUT, ""),
            "BR": CLIResult(0, CITIES_BR_STDOUT, ""),
        },
    )
    catalog = ServerCatalog(cli)
    catalog.prewarm()
    # Wait for prewarm to complete
    if catalog._prewarm_task:
        await catalog._prewarm_task
    assert set(cli.cities_calls) == {"IS", "US", "BR"}
    for code in ["IS", "US", "BR"]:
        assert catalog._entries[code].state is EntryState.LOADED


@pytest.mark.asyncio
async def test_prewarm_continues_after_partial_failure(qapp):
    """One bad country should not abort the prewarm for the rest."""

    async def flaky_cities(code):
        if code == "IS":
            return CLIResult(2, "", "some error")
        if code == "US":
            return CLIResult(0, CITIES_US_STDOUT, "")
        if code == "BR":
            return CLIResult(0, CITIES_BR_STDOUT, "")
        return CLIResult(0, "", "")

    cli = ScriptedCLI(CLIResult(0, COUNTRIES_STDOUT, ""))
    cli.cities_list = flaky_cities  # type: ignore[method-assign]
    catalog = ServerCatalog(cli)
    catalog.prewarm()
    if catalog._prewarm_task:
        await catalog._prewarm_task

    assert catalog._entries["IS"].state is EntryState.FAILED
    assert catalog._entries["US"].state is EntryState.LOADED
    assert catalog._entries["BR"].state is EntryState.LOADED


@pytest.mark.asyncio
async def test_prewarm_deduplicates(qapp):
    cli = make_cli()
    catalog = ServerCatalog(cli)
    catalog.prewarm()
    catalog.prewarm()  # second call is a no-op while first is running
    if catalog._prewarm_task:
        await catalog._prewarm_task
    # Countries were fetched exactly once
    assert cli.countries_calls == 1
