"""ServerCatalog: session-scoped, in-memory server catalog.

Fetches country and city data from the protonvpn CLI on demand.
Thread model: Qt main thread + qasync; all asyncio tasks run on the
qasync event loop. Never call cities_sync() or asyncio.run() from
within a running event loop.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime

from PyQt6.QtCore import QObject, pyqtSignal

from ..cli import ProtonCLI
from .models import CatalogEntry, CatalogError, City, Country, EntryState
from .parser import parse_cities, parse_countries

log = logging.getLogger(__name__)

_CODE_RE = re.compile(r"^[A-Z]{2}$")


class ServerCatalog(QObject):
    """In-memory catalog of Proton countries and cities.

    Signals
    -------
    catalog_changed(country_code: str)
        Emitted when any entry transitions state.  country_code is the
        two-letter code for a city entry, or an empty string when the
        countries list itself changes.
    """

    catalog_changed = pyqtSignal(str)

    def __init__(self, cli: ProtonCLI, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cli = cli
        self._countries: list[Country] | None = None
        self._countries_error: str | None = None
        self._countries_task: asyncio.Task | None = None
        self._entries: dict[str, CatalogEntry] = {}
        self._loading_tasks: dict[str, asyncio.Task] = {}
        self._prewarm_task: asyncio.Task | None = None

    # ----- public API -----

    async def countries(self) -> list[Country]:
        """Return the cached country list, fetching on first call.

        Raises CatalogError if the fetch fails (e.g. signed-out).
        Concurrent callers share the same in-flight task.
        """
        if self._countries is not None:
            return self._countries
        if self._countries_task is None or self._countries_task.done():
            self._countries_task = asyncio.create_task(self._fetch_countries())
        await self._countries_task
        if self._countries is None:
            raise CatalogError(self._countries_error or "Failed to load countries")
        return self._countries

    def cities(self, country_code: str) -> CatalogEntry:
        """Return the current CatalogEntry for country_code.

        Kicks off a background fetch if the entry is NOT_FETCHED.
        Returns immediately — callers must re-call (or await catalog_changed)
        to observe the LOADED/FAILED transition.

        Raises CatalogError immediately for malformed country codes.
        Concurrent calls for the same code dedup to one fetch.
        """
        code = country_code.upper()
        if not _CODE_RE.match(code):
            raise CatalogError(f"Invalid country code: {country_code!r}")
        if code not in self._entries:
            self._entries[code] = CatalogEntry(country_code=code)
        entry = self._entries[code]
        if entry.state is EntryState.NOT_FETCHED and code not in self._loading_tasks:
            task = asyncio.create_task(self._fetch_cities(code))
            self._loading_tasks[code] = task
            entry.state = EntryState.LOADING
        return entry

    async def cities_async(self, country_code: str) -> list[City]:
        """Await until cities for country_code are loaded.

        Kicks off a fetch if needed, then awaits completion.
        Raises CatalogError on failure.
        """
        code = country_code.upper()
        entry = self.cities(code)
        if code in self._loading_tasks:
            await self._loading_tasks[code]
        entry = self._entries[code]
        if entry.state is EntryState.LOADED:
            return entry.cities
        raise CatalogError(entry.last_error or f"Failed to load cities for {code}")

    def cities_sync(self, country_code: str) -> list[City]:
        """Blocking fetch. For use outside the Qt event loop (tests, dump command)."""
        return asyncio.run(self._fetch_cities_direct(country_code))

    def prewarm(self) -> None:
        """Start a background task that fetches all countries' cities sequentially.

        Throttled to one country at a time to avoid hammering the Proton API.
        Safe to call multiple times; only one prewarm task runs at a time.
        """
        if self._prewarm_task is None or self._prewarm_task.done():
            self._prewarm_task = asyncio.create_task(
                self._prewarm_loop(), name="vpnpilot-catalog-prewarm"
            )

    def refresh(self) -> None:
        """Drop the cache and allow a fresh fetch on next access."""
        self._countries = None
        self._countries_error = None
        self._countries_task = None
        self._entries.clear()
        self._loading_tasks.clear()
        if self._prewarm_task and not self._prewarm_task.done():
            self._prewarm_task.cancel()
        self._prewarm_task = None

    # ----- internals -----

    async def _fetch_countries(self) -> None:
        result = await self._cli.countries_list()
        if result.ok:
            self._countries = parse_countries(result.stdout)
            self._countries_error = None
        else:
            self._countries = None
            self._countries_error = result.stderr.strip()
            log.warning("countries list failed (exit %d): %s", result.returncode, self._countries_error)
        self.catalog_changed.emit("")

    async def _fetch_cities(self, country_code: str) -> None:
        result = await self._cli.cities_list(country_code)
        entry = self._entries[country_code]
        if result.ok:
            entry.cities = parse_cities(result.stdout, country_code)
            entry.state = EntryState.LOADED
            entry.last_fetched_at = datetime.now(UTC)
            entry.last_error = None
        else:
            entry.state = EntryState.FAILED
            entry.last_error = result.stderr.strip()
            log.warning("cities list %s failed (exit %d): %s", country_code, result.returncode, entry.last_error)
        self._loading_tasks.pop(country_code, None)
        self.catalog_changed.emit(country_code)

    async def _fetch_cities_direct(self, country_code: str) -> list[City]:
        """Standalone fetch with no cache interaction; used by cities_sync."""
        code = country_code.upper()
        result = await self._cli.cities_list(code)
        if not result.ok:
            raise CatalogError(result.stderr.strip())
        return parse_cities(result.stdout, code)

    async def _prewarm_loop(self) -> None:
        try:
            countries = await self.countries()
        except CatalogError as exc:
            log.warning("prewarm aborted: %s", exc)
            return
        for country in countries:
            try:
                await self.cities_async(country.code)
            except CatalogError as exc:
                log.warning("prewarm: failed to fetch cities for %s: %s", country.code, exc)
