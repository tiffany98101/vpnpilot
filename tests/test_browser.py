"""Tests for the BrowseTab widget (browser.py).

All catalog access is via a FakeCatalog test double that mirrors the
ServerCatalog public API without any subprocess calls.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from vpnpilot.browser import BrowseTab, CityListModel, CountryListModel
from vpnpilot.catalog.models import CatalogEntry, City, CityFeature, Country, EntryState
from vpnpilot.state import AuthState, ConnectionInfo, ConnState

# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


class FakeController(QObject):
    """Synchronous fake controller — records connect calls without asyncio."""

    state_changed = pyqtSignal(object)
    error_occurred = pyqtSignal(str)

    def __init__(self, auth: AuthState = AuthState.SIGNED_IN) -> None:
        super().__init__()
        self._current = ConnectionInfo(state=ConnState.DISCONNECTED, auth=auth)
        self.connect_location_calls: list[tuple] = []
        self.connect_server_id_calls: list[str] = []

    @property
    def current(self) -> ConnectionInfo:
        return self._current

    def connect_to_location(self, country_code: str, city: str | None = None) -> None:
        self.connect_location_calls.append((country_code, city))

    def connect_to_server_id(self, server_id: str) -> None:
        from vpnpilot.controller import _SERVER_ID_RE
        normalized = server_id.strip().upper()
        if not _SERVER_ID_RE.match(normalized):
            raise ValueError(f"Invalid server ID: {server_id!r}")
        self.connect_server_id_calls.append(normalized)

    def disconnect(self) -> None:
        pass


def _make_controller(auth: AuthState = AuthState.SIGNED_IN) -> FakeController:
    return FakeController(auth=auth)


class FakeCatalog(QObject):
    """Minimal catalog double for browser tests."""

    catalog_changed = pyqtSignal(str)

    def __init__(self, countries: list[Country] | None = None) -> None:
        super().__init__()
        self._countries = countries
        self._entries: dict[str, CatalogEntry] = {}
        self.refresh_called = 0
        self.cities_calls: list[str] = []

    def countries_if_ready(self) -> list[Country] | None:
        return self._countries

    def cities_if_loaded(self, code: str):
        entry = self._entries.get(code.upper())
        if entry and entry.state is EntryState.LOADED:
            return entry.cities
        return None

    def entry_state(self, code: str) -> EntryState:
        entry = self._entries.get(code.upper())
        return entry.state if entry else EntryState.NOT_FETCHED

    def entry_error(self, code: str) -> str | None:
        entry = self._entries.get(code.upper())
        return entry.last_error if entry else None

    def cities(self, code: str):
        self.cities_calls.append(code.upper())
        return self._entries.get(code.upper(), CatalogEntry(country_code=code.upper()))

    def refresh(self) -> None:
        self._refresh_called = True
        self.refresh_called += 1
        self._countries = None

    async def countries(self):
        if self._countries is None:
            from vpnpilot.catalog.models import CatalogError
            raise CatalogError("not loaded")
        return self._countries

    def set_entry_loaded(self, code: str, cities: list[City]) -> None:
        code = code.upper()
        entry = CatalogEntry(country_code=code, state=EntryState.LOADED, cities=cities)
        self._entries[code] = entry

    def set_entry_loading(self, code: str) -> None:
        code = code.upper()
        entry = CatalogEntry(country_code=code, state=EntryState.LOADING)
        self._entries[code] = entry

    def set_entry_failed(self, code: str, error: str) -> None:
        code = code.upper()
        entry = CatalogEntry(country_code=code, state=EntryState.FAILED, last_error=error)
        self._entries[code] = entry


_COUNTRIES = [
    Country(code="DE", name="Germany"),
    Country(code="US", name="United States"),
    Country(code="IS", name="Iceland"),
]

_DE_CITIES = [
    City(name="Berlin", country_code="DE"),
    City(name="Frankfurt", country_code="DE", features=frozenset({CityFeature.P2P})),
]

_US_CITIES = [
    City(name="Atlanta", country_code="US", features=frozenset({CityFeature.P2P, CityFeature.TOR})),
    City(name="Seattle", country_code="US", features=frozenset({CityFeature.P2P})),
]
_DUPLICATE_CITY = "Springfield"


# ---------------------------------------------------------------------------
# CountryListModel tests
# ---------------------------------------------------------------------------


def test_country_model_populates(qapp):
    cat = FakeCatalog(countries=_COUNTRIES)
    model = CountryListModel(cat)
    model.set_countries(_COUNTRIES)
    assert model.rowCount() == 3
    # Sorted alphabetically by name.
    names = [model.data(model.index(i), model.CountryRole).name for i in range(3)]
    assert names == ["Germany", "Iceland", "United States"]


def test_country_model_display_shows_code(qapp):
    cat = FakeCatalog(countries=_COUNTRIES)
    model = CountryListModel(cat)
    model.set_countries([Country(code="DE", name="Germany")])
    text = model.data(model.index(0))
    assert "Germany" in text
    assert "DE" in text


def test_country_model_loading_suffix(qapp):
    cat = FakeCatalog(countries=_COUNTRIES)
    cat.set_entry_loading("DE")
    model = CountryListModel(cat)
    model.set_countries([Country(code="DE", name="Germany")])
    text = model.data(model.index(0))
    assert "…" in text


def test_country_model_failed_suffix(qapp):
    cat = FakeCatalog(countries=_COUNTRIES)
    cat.set_entry_failed("DE", "auth error")
    model = CountryListModel(cat)
    model.set_countries([Country(code="DE", name="Germany")])
    text = model.data(model.index(0))
    assert "⚠" in text


# ---------------------------------------------------------------------------
# CityListModel tests
# ---------------------------------------------------------------------------


def test_city_model_set_loaded(qapp):
    model = CityListModel()
    model.set_loaded(_DE_CITIES)
    assert model.rowCount() == 2


def test_city_model_loading_placeholder(qapp):
    model = CityListModel()
    model.set_loading()
    assert model.rowCount() == 1
    assert "loading" in model.data(model.index(0)).lower()


def test_city_model_failed_placeholder(qapp):
    model = CityListModel()
    model.set_failed("auth error")
    assert model.rowCount() == 1
    assert "failed" in model.data(model.index(0)).lower()


def test_city_model_feature_badges(qapp):
    model = CityListModel()
    model.set_loaded(_US_CITIES)
    # Atlanta has P2P and Tor features.
    texts = [model.data(model.index(i)) for i in range(model.rowCount())]
    atlanta_text = next(t for t in texts if "Atlanta" in t)
    assert "P2P" in atlanta_text
    assert "Tor" in atlanta_text


# ---------------------------------------------------------------------------
# BrowseTab integration tests
# ---------------------------------------------------------------------------


def test_browse_tab_populates_countries_from_cache(qapp, qtbot):
    cat = FakeCatalog(countries=_COUNTRIES)
    ctrl = _make_controller()
    tab = BrowseTab(catalog=cat, controller=ctrl)
    qtbot.addWidget(tab)
    assert tab._country_model.rowCount() == 3


def test_browse_tab_country_filter_narrows_list(qapp, qtbot):
    cat = FakeCatalog(countries=_COUNTRIES)
    ctrl = _make_controller()
    tab = BrowseTab(catalog=cat, controller=ctrl)
    qtbot.addWidget(tab)
    tab.country_filter.setText("german")
    # Proxy should show only Germany.
    assert tab._country_proxy.rowCount() == 1
    idx = tab._country_proxy.index(0, 0)
    country = tab._country_proxy.data(idx, CountryListModel.CountryRole)
    assert country.name == "Germany"


def test_browse_tab_selecting_country_triggers_cities_request(qapp, qtbot):
    cat = FakeCatalog(countries=_COUNTRIES)
    cat.set_entry_loaded("DE", _DE_CITIES)
    ctrl = _make_controller()
    tab = BrowseTab(catalog=cat, controller=ctrl)
    qtbot.addWidget(tab)

    # Select Germany (row 0 after sort: Germany, Iceland, United States).
    tab.country_list.setCurrentIndex(tab._country_proxy.index(0, 0))
    # catalog.cities() should have been called for DE.
    assert "DE" in cat.cities_calls


def test_browse_tab_cities_pane_shows_loaded_cities(qapp, qtbot):
    cat = FakeCatalog(countries=_COUNTRIES)
    cat.set_entry_loaded("DE", _DE_CITIES)
    ctrl = _make_controller()
    tab = BrowseTab(catalog=cat, controller=ctrl)
    qtbot.addWidget(tab)

    # Select Germany.
    tab.country_list.setCurrentIndex(tab._country_proxy.index(0, 0))
    assert tab._city_model.rowCount() == 2


def test_browse_tab_cities_pane_shows_feature_badges(qapp, qtbot):
    cat = FakeCatalog(countries=_COUNTRIES)
    cat.set_entry_loaded("US", _US_CITIES)
    ctrl = _make_controller()
    tab = BrowseTab(catalog=cat, controller=ctrl)
    qtbot.addWidget(tab)
    tab.show()

    # Select United States (row 2 after sort: Germany, Iceland, United States).
    tab.country_list.setCurrentIndex(tab._country_proxy.index(2, 0))
    texts = [tab._city_model.data(tab._city_model.index(i)) for i in range(tab._city_model.rowCount())]
    atlanta_text = next(t for t in texts if "Atlanta" in t)
    assert "P2P" in atlanta_text
    assert "Tor" in atlanta_text


def test_browse_tab_double_click_calls_connect_to_location(qapp, qtbot):
    cat = FakeCatalog(countries=_COUNTRIES)
    cat.set_entry_loaded("DE", _DE_CITIES)
    ctrl = _make_controller()
    tab = BrowseTab(catalog=cat, controller=ctrl)
    qtbot.addWidget(tab)

    # Select Germany, then a city.
    tab.country_list.setCurrentIndex(tab._country_proxy.index(0, 0))
    tab.city_list.setCurrentIndex(tab._city_proxy.index(0, 0))

    # Call the double-click handler directly.
    tab._on_city_double_clicked(tab._city_proxy.index(0, 0))

    assert len(ctrl.connect_location_calls) == 1
    country_code, city = ctrl.connect_location_calls[0]
    assert city is not None  # a specific city was passed


def test_browse_tab_connect_btn_country_only(qapp, qtbot):
    cat = FakeCatalog(countries=_COUNTRIES)
    cat.set_entry_loaded("DE", _DE_CITIES)
    ctrl = _make_controller()
    tab = BrowseTab(catalog=cat, controller=ctrl)
    qtbot.addWidget(tab)

    # Select Germany but no city.
    tab.country_list.setCurrentIndex(tab._country_proxy.index(0, 0))
    tab.city_list.clearSelection()

    tab._on_connect_clicked()
    assert len(ctrl.connect_location_calls) == 1
    country_code, city = ctrl.connect_location_calls[0]
    assert country_code == "DE"
    assert city is None


def test_browse_tab_city_hint_marks_best_effort(qapp, qtbot):
    cat = FakeCatalog(countries=_COUNTRIES)
    cat.set_entry_loaded(
        "DE",
        [City(name=_DUPLICATE_CITY, country_code="DE")],
    )
    ctrl = _make_controller()
    tab = BrowseTab(catalog=cat, controller=ctrl)
    qtbot.addWidget(tab)

    tab.country_list.setCurrentIndex(tab._country_proxy.index(0, 0))
    tab.city_list.setCurrentIndex(tab._city_proxy.index(0, 0))
    tab._on_city_selection_changed()

    hint = tab.hint_label.text().lower()
    assert "best-effort" in hint
    assert _DUPLICATE_CITY.lower() in hint


def test_browse_tab_duplicate_city_names_still_pass_selected_country(qapp, qtbot):
    cat = FakeCatalog(countries=_COUNTRIES)
    cat.set_entry_loaded("DE", [City(name=_DUPLICATE_CITY, country_code="DE")])
    cat.set_entry_loaded("US", [City(name=_DUPLICATE_CITY, country_code="US")])
    ctrl = _make_controller()
    tab = BrowseTab(catalog=cat, controller=ctrl)
    qtbot.addWidget(tab)

    # Germany row is index 0 after sort.
    tab.country_list.setCurrentIndex(tab._country_proxy.index(0, 0))
    tab.city_list.setCurrentIndex(tab._city_proxy.index(0, 0))
    tab._on_connect_clicked()

    # United States row is index 2 after sort.
    tab.country_list.setCurrentIndex(tab._country_proxy.index(2, 0))
    tab.city_list.setCurrentIndex(tab._city_proxy.index(0, 0))
    tab._on_connect_clicked()

    assert ctrl.connect_location_calls == [
        ("DE", _DUPLICATE_CITY),
        ("US", _DUPLICATE_CITY),
    ]


def test_browse_tab_server_id_valid_calls_connect(qapp, qtbot):
    cat = FakeCatalog(countries=_COUNTRIES)
    ctrl = _make_controller()
    tab = BrowseTab(catalog=cat, controller=ctrl)
    qtbot.addWidget(tab)

    tab.server_id_edit.setText("US-WA#232")
    tab._on_server_id_connect()

    assert ctrl.connect_server_id_calls == ["US-WA#232"]


def test_browse_tab_server_id_invalid_no_controller_call(qapp, qtbot):
    cat = FakeCatalog(countries=_COUNTRIES)
    ctrl = _make_controller()
    tab = BrowseTab(catalog=cat, controller=ctrl)
    qtbot.addWidget(tab)

    tab.server_id_edit.setText("NOTAVALIDID")
    tab._on_server_id_connect()

    # Controller should not have been called.
    assert ctrl.connect_server_id_calls == []
    assert "Invalid" in tab.hint_label.text()


def test_browse_tab_refresh_calls_catalog_refresh(qapp, qtbot):
    cat = FakeCatalog(countries=_COUNTRIES)
    ctrl = _make_controller()
    tab = BrowseTab(catalog=cat, controller=ctrl)
    qtbot.addWidget(tab)

    tab._on_refresh()
    assert cat.refresh_called == 1


def test_browse_tab_signed_out_shows_auth_page(qapp, qtbot):
    cat = FakeCatalog(countries=_COUNTRIES)
    ctrl = _make_controller(auth=AuthState.SIGNED_OUT)
    tab = BrowseTab(catalog=cat, controller=ctrl)
    qtbot.addWidget(tab)

    info = ConnectionInfo(state=ConnState.DISCONNECTED, auth=AuthState.SIGNED_OUT)
    tab._on_state_changed(info)
    # Stack index 0 is the auth page.
    assert tab._stack.currentIndex() == 0


def test_browse_tab_signed_in_shows_normal_page(qapp, qtbot):
    cat = FakeCatalog(countries=_COUNTRIES)
    ctrl = _make_controller()
    tab = BrowseTab(catalog=cat, controller=ctrl)
    qtbot.addWidget(tab)

    info = ConnectionInfo(state=ConnState.DISCONNECTED, auth=AuthState.SIGNED_IN)
    tab._on_state_changed(info)
    assert tab._stack.currentIndex() == 1


def test_browse_tab_catalog_changed_updates_country_model(qapp, qtbot):
    cat = FakeCatalog(countries=None)
    ctrl = _make_controller()
    tab = BrowseTab(catalog=cat, controller=ctrl)
    qtbot.addWidget(tab)

    assert tab._country_model.rowCount() == 0

    # Simulate catalog loading countries and emitting the signal.
    cat._countries = _COUNTRIES
    tab._on_catalog_changed("")

    assert tab._country_model.rowCount() == 3


def test_browse_tab_catalog_changed_updates_cities_for_selected_country(qapp, qtbot):
    cat = FakeCatalog(countries=_COUNTRIES)
    cat.set_entry_loading("DE")
    ctrl = _make_controller()
    tab = BrowseTab(catalog=cat, controller=ctrl)
    qtbot.addWidget(tab)

    # Select Germany (loading state initially).
    tab.country_list.setCurrentIndex(tab._country_proxy.index(0, 0))
    assert tab._city_model._state is EntryState.LOADING

    # Simulate cities arriving.
    cat.set_entry_loaded("DE", _DE_CITIES)
    tab._on_catalog_changed("DE")

    assert tab._city_model._state is EntryState.LOADED
    assert tab._city_model.rowCount() == 2
