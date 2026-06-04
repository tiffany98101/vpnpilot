"""Server browser: two-pane country/city catalog view.

The tab is secondary to the preset list — it's a discovery surface,
not the daily-use connect path. It sources data from ServerCatalog via
signals (catalog_changed) and non-triggering accessors; it never calls
catalog.countries() or catalog.cities_async() directly (the prewarm
loop does that). When a country is selected, it requests the city list
via catalog.cities() which kicks off a background fetch if needed.

Threading: all updates run on the Qt main thread via signals.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from PyQt6.QtCore import QAbstractListModel, QModelIndex, QSortFilterProxyModel, Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .catalog import ServerCatalog
from .catalog.models import CatalogError, CityFeature, Country, EntryState
from .controller import Controller
from .state import AuthState, ConnectionInfo

log = logging.getLogger(__name__)

# Minimum column widths so the splitter doesn't collapse panes.
_MIN_COUNTRY_WIDTH = 140
_MIN_CITY_WIDTH = 200
_CITY_BEST_EFFORT_NOTE = "city match is best-effort via Proton CLI"


# --------------------------------------------------------------------------
# Country model
# --------------------------------------------------------------------------


class CountryListModel(QAbstractListModel):
    """Flat, sorted list of countries. Displays name + code."""

    CountryRole = Qt.ItemDataRole.UserRole + 1

    def __init__(self, catalog: ServerCatalog, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._catalog = catalog
        self._countries: list[Country] = []

    def set_countries(self, countries: list[Country]) -> None:
        self.beginResetModel()
        self._countries = sorted(countries, key=lambda c: c.name)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        if parent is not None and parent.isValid():
            return 0
        return len(self._countries)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._countries):
            return None
        country = self._countries[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            state = self._catalog.entry_state(country.code)
            suffix = ""
            if state is EntryState.LOADING:
                suffix = "  …"
            elif state is EntryState.FAILED:
                suffix = "  ⚠"
            return f"{country.name}  {country.code}{suffix}"
        if role == Qt.ItemDataRole.ToolTipRole:
            state = self._catalog.entry_state(country.code)
            if state is EntryState.FAILED:
                err = self._catalog.entry_error(country.code)
                return err or "Failed to load city list"
        if role == self.CountryRole:
            return country
        return None

    def update_country_row(self, country_code: str) -> None:
        """Refresh the display role for a single country code."""
        code = country_code.upper()
        for row, country in enumerate(self._countries):
            if country.code == code:
                idx = self.index(row, 0)
                self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DisplayRole])
                return

    def country_at(self, row: int) -> Country | None:
        if 0 <= row < len(self._countries):
            return self._countries[row]
        return None


# --------------------------------------------------------------------------
# City model
# --------------------------------------------------------------------------

_FEATURE_LABELS: dict[CityFeature, str] = {
    CityFeature.P2P: "P2P",
    CityFeature.TOR: "Tor",
}


class CityListModel(QAbstractListModel):
    """Cities for a selected country, with loading/failed placeholder rows."""

    CityRole = Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state: EntryState = EntryState.NOT_FETCHED
        self._cities: list = []
        self._error: str = ""

    def set_loading(self) -> None:
        self.beginResetModel()
        self._state = EntryState.LOADING
        self._cities = []
        self._error = ""
        self.endResetModel()

    def set_loaded(self, cities: list) -> None:
        self.beginResetModel()
        self._state = EntryState.LOADED
        self._cities = sorted(cities, key=lambda c: c.name)
        self._error = ""
        self.endResetModel()

    def set_failed(self, error: str) -> None:
        self.beginResetModel()
        self._state = EntryState.FAILED
        self._cities = []
        self._error = error
        self.endResetModel()

    def clear(self) -> None:
        self.beginResetModel()
        self._state = EntryState.NOT_FETCHED
        self._cities = []
        self._error = ""
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        if parent is not None and parent.isValid():
            return 0
        if self._state in (EntryState.LOADING, EntryState.FAILED):
            return 1
        return len(self._cities)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if self._state is EntryState.LOADING:
            if role == Qt.ItemDataRole.DisplayRole:
                return "Loading cities…"
            return None
        if self._state is EntryState.FAILED:
            if role == Qt.ItemDataRole.DisplayRole:
                return f"Failed: {self._error}" if self._error else "Failed to load cities"
            return None
        row = index.row()
        if row >= len(self._cities):
            return None
        city = self._cities[row]
        if role == Qt.ItemDataRole.DisplayRole:
            badges = "  ".join(_FEATURE_LABELS.get(f, str(f)) for f in sorted(city.features))
            return f"{city.name}  [{badges}]" if badges else city.name
        if role == self.CityRole:
            return city
        return None

    def city_at(self, row: int):
        if self._state is EntryState.LOADED and 0 <= row < len(self._cities):
            return self._cities[row]
        return None


# --------------------------------------------------------------------------
# BrowseTab
# --------------------------------------------------------------------------


class BrowseTab(QWidget):
    """Two-pane country/city browser backed by ServerCatalog.

    Subscribes to controller.state_changed (for auth) and
    catalog.catalog_changed (for incremental updates). Never directly
    awaits catalog coroutines — schedules tasks on the running loop.
    """

    def __init__(
        self,
        catalog: ServerCatalog,
        controller: Controller,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._catalog = catalog
        self._controller = controller
        self._selected_country: Country | None = None

        # ---- stack: normal page vs auth-required page ----
        self._stack = QStackedWidget()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._stack)

        # ---- auth-required page ----
        auth_page = QWidget()
        auth_layout = QVBoxLayout(auth_page)
        auth_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        auth_label = QLabel(
            "Sign in to browse servers.\n\nUse the Sign in… option in the tray menu."
        )
        auth_label.setObjectName("browseAuthNotice")
        auth_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        auth_label.setStyleSheet("color: #888; font-size: 11pt;")
        auth_layout.addWidget(auth_label)
        self._stack.addWidget(auth_page)

        # ---- normal page ----
        normal_page = QWidget()
        normal_layout = QVBoxLayout(normal_page)
        normal_layout.setContentsMargins(4, 4, 4, 4)
        normal_layout.setSpacing(4)

        # Splitter: countries left, cities right
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("browseSplitter")

        # Left: countries
        country_pane = QWidget()
        country_pane.setMinimumWidth(_MIN_COUNTRY_WIDTH)
        cl = QVBoxLayout(country_pane)
        cl.setContentsMargins(0, 0, 4, 0)
        cl.setSpacing(4)

        self.country_filter = QLineEdit()
        self.country_filter.setObjectName("countryFilter")
        self.country_filter.setPlaceholderText("Filter countries…")
        cl.addWidget(self.country_filter)

        self._country_model = CountryListModel(catalog, self)
        self._country_proxy = QSortFilterProxyModel(self)
        self._country_proxy.setSourceModel(self._country_model)
        self._country_proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._country_proxy.setFilterKeyColumn(0)

        self.country_list = QListView()
        self.country_list.setObjectName("countryList")
        self.country_list.setModel(self._country_proxy)
        self.country_list.setSelectionMode(QListView.SelectionMode.SingleSelection)
        cl.addWidget(self.country_list, stretch=1)
        splitter.addWidget(country_pane)

        # Right: cities
        city_pane = QWidget()
        city_pane.setMinimumWidth(_MIN_CITY_WIDTH)
        city_layout = QVBoxLayout(city_pane)
        city_layout.setContentsMargins(4, 0, 0, 0)
        city_layout.setSpacing(4)

        self.city_header = QLabel("Select a country")
        self.city_header.setObjectName("cityHeader")
        self.city_header.setStyleSheet("font-weight: 600;")
        city_layout.addWidget(self.city_header)

        self.city_filter = QLineEdit()
        self.city_filter.setObjectName("cityFilter")
        self.city_filter.setPlaceholderText("Filter cities…")
        city_layout.addWidget(self.city_filter)

        self._city_model = CityListModel(self)
        self._city_proxy = QSortFilterProxyModel(self)
        self._city_proxy.setSourceModel(self._city_model)
        self._city_proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._city_proxy.setFilterKeyColumn(0)

        self.city_list = QListView()
        self.city_list.setObjectName("cityList")
        self.city_list.setModel(self._city_proxy)
        self.city_list.setSelectionMode(QListView.SelectionMode.SingleSelection)
        city_layout.addWidget(self.city_list, stretch=1)
        splitter.addWidget(city_pane)

        splitter.setStretchFactor(0, 40)
        splitter.setStretchFactor(1, 60)
        normal_layout.addWidget(splitter, stretch=1)

        # Footer
        footer_frame = QFrame()
        footer_frame.setObjectName("browseFooter")
        footer_frame.setFrameShape(QFrame.Shape.StyledPanel)
        footer = QHBoxLayout(footer_frame)
        footer.setContentsMargins(8, 4, 8, 4)

        self.hint_label = QLabel("Select a country or city to connect.")
        self.hint_label.setObjectName("browseHint")
        self.hint_label.setStyleSheet("color: #666;")
        footer.addWidget(self.hint_label, stretch=1)

        footer.addSpacing(8)

        self.server_id_edit = QLineEdit()
        self.server_id_edit.setObjectName("serverIdEdit")
        self.server_id_edit.setPlaceholderText("Server ID (US-WA#232)")
        self.server_id_edit.setFixedWidth(160)
        footer.addWidget(self.server_id_edit)

        self.server_id_btn = QPushButton("Connect by ID")
        self.server_id_btn.setObjectName("serverIdConnect")
        footer.addWidget(self.server_id_btn)

        footer.addSpacing(8)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("browseConnect")
        self.connect_btn.setEnabled(False)
        footer.addWidget(self.connect_btn)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setObjectName("browseRefresh")
        footer.addWidget(self.refresh_btn)

        normal_layout.addWidget(footer_frame)
        self._stack.addWidget(normal_page)

        # start on normal page
        self._stack.setCurrentIndex(1)

        # ---- wire signals ----
        self._catalog.catalog_changed.connect(self._on_catalog_changed)
        self._controller.state_changed.connect(self._on_state_changed)

        self.country_filter.textChanged.connect(
            lambda t: self._country_proxy.setFilterFixedString(t)
        )
        self.city_filter.textChanged.connect(lambda t: self._city_proxy.setFilterFixedString(t))
        country_sel = self.country_list.selectionModel()
        country_sel.selectionChanged.connect(self._on_country_selection_changed)
        city_sel = self.city_list.selectionModel()
        city_sel.selectionChanged.connect(self._on_city_selection_changed)
        self.city_list.doubleClicked.connect(self._on_city_double_clicked)
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        self.server_id_btn.clicked.connect(self._on_server_id_connect)
        self.refresh_btn.clicked.connect(self._on_refresh)

        # Trigger initial load if data is already cached.
        self._try_populate_from_cache()

    # ----- public -----

    def on_shown(self) -> None:
        """Call when the tab becomes visible to trigger a load if needed."""
        self._schedule_load_countries()

    def prepare_for_catalog_refresh(self, hint: str = "Refreshing…") -> None:
        """Clear transient selection while the shared catalog is being refreshed."""
        self.country_list.clearSelection()
        self.city_list.clearSelection()
        self._city_model.clear()
        self._selected_country = None
        self.city_header.setText("Select a country")
        self.connect_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self.hint_label.setText(hint)

    def finish_catalog_refresh(self, hint: str | None = None) -> None:
        """Restore controls after a shared catalog refresh finishes or aborts."""
        self.refresh_btn.setEnabled(True)
        if hint is not None:
            self.hint_label.setText(hint)

    # ----- connection-state handling -----

    def _on_state_changed(self, info: ConnectionInfo) -> None:
        if info.auth is AuthState.SIGNED_OUT:
            self._stack.setCurrentIndex(0)  # auth page
        else:
            self._stack.setCurrentIndex(1)  # normal page
            self._schedule_load_countries()

    # ----- catalog signal handler -----

    def _on_catalog_changed(self, country_code: str) -> None:
        if country_code == "":
            # Countries list updated.
            countries = self._catalog.countries_if_ready()
            self.finish_catalog_refresh(
                None if countries is not None else "Could not load countries."
            )
            if countries is not None:
                self._country_model.set_countries(countries)
        else:
            # Cities for a specific country updated.
            self._country_model.update_country_row(country_code)
            if (
                self._selected_country is not None
                and self._selected_country.code == country_code.upper()
            ):
                self._update_cities_pane()

    # ----- country selection -----

    def _on_country_selection_changed(self, *_) -> None:
        country = self._selected_country_from_view()
        self._selected_country = country
        if country is None:
            self.city_header.setText("Select a country")
            self._city_model.clear()
            self.connect_btn.setEnabled(False)
            self.hint_label.setText("Select a country or city to connect.")
            return

        # Kick off a fetch if not already loaded. The catalog_changed
        # signal will fire _update_cities_pane when data arrives.
        self._catalog.cities(country.code)
        self._update_cities_pane()
        self._update_hint()
        self.connect_btn.setEnabled(True)

    def _on_city_selection_changed(self, *_) -> None:
        self._update_hint()
        self.connect_btn.setEnabled(self._selected_country is not None)

    def _on_city_double_clicked(self, proxy_index: QModelIndex) -> None:
        # Double-click on a city directly connects.
        city = self._selected_city_from_view()
        country = self._selected_country
        if city is not None and country is not None:
            self._controller.connect_to_location(country.code, city.name)

    # ----- connect / server-ID -----

    def _on_connect_clicked(self) -> None:
        city = self._selected_city_from_view()
        country = self._selected_country
        if country is None:
            return
        if city is not None:
            self._controller.connect_to_location(country.code, city.name)
        else:
            self._controller.connect_to_location(country.code)

    def _on_server_id_connect(self) -> None:
        raw = self.server_id_edit.text().strip()
        if not raw:
            self.hint_label.setText("Type a server ID like US-WA#232.")
            return
        try:
            self._controller.connect_to_server_id(raw)
        except ValueError:
            self.hint_label.setText(f'Invalid server ID "{raw}". Format: US-WA#232 or IT#23.')

    def _on_refresh(self) -> None:
        self.prepare_for_catalog_refresh()
        self._catalog.refresh()
        self._schedule_load_countries()
        # Re-enable once the catalog_changed("") arrives.
        # The signal handler will update the model; reset button state there.

    # ----- internals -----

    def _selected_country_from_view(self) -> Country | None:
        rows = self.country_list.selectionModel().selectedRows()
        if not rows:
            current = self.country_list.currentIndex()
            if not current.isValid():
                return None
            rows = [current]
        proxy_idx = rows[0]
        source_idx = self._country_proxy.mapToSource(proxy_idx)
        return self._country_model.country_at(source_idx.row())

    def _selected_city_from_view(self):
        rows = self.city_list.selectionModel().selectedRows()
        if not rows:
            current = self.city_list.currentIndex()
            if not current.isValid():
                return None
            rows = [current]
        proxy_idx = rows[0]
        source_idx = self._city_proxy.mapToSource(proxy_idx)
        return self._city_model.city_at(source_idx.row())

    def _update_cities_pane(self) -> None:
        country = self._selected_country
        if country is None:
            return
        state = self._catalog.entry_state(country.code)
        if state is EntryState.LOADED:
            cities = self._catalog.cities_if_loaded(country.code) or []
            self._city_model.set_loaded(cities)
            self.city_header.setText(f"{country.name}  ({len(cities)} cities)")
            self.refresh_btn.setEnabled(True)
        elif state is EntryState.LOADING:
            self._city_model.set_loading()
            self.city_header.setText(f"{country.name}  (loading…)")
        elif state is EntryState.FAILED:
            err = self._catalog.entry_error(country.code) or ""
            self._city_model.set_failed(err)
            self.city_header.setText(f"{country.name}  (error)")
        else:
            self._city_model.clear()
            self.city_header.setText(f"{country.name}")

    def _update_hint(self) -> None:
        city = self._selected_city_from_view()
        country = self._selected_country
        if country is None:
            self.hint_label.setText("Select a country or city to connect.")
        elif city is not None:
            self.hint_label.setText(
                f"Selected: {country.name} / {city.name} ({_CITY_BEST_EFFORT_NOTE})"
            )
        else:
            self.hint_label.setText(f"Selected: {country.name}")

    def _try_populate_from_cache(self) -> None:
        countries = self._catalog.countries_if_ready()
        if countries is not None:
            self._country_model.set_countries(countries)
        else:
            self._schedule_load_countries()

    def _schedule_load_countries(self) -> None:
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().create_task(
                self._load_countries_async(), name="vpnpilot-browse-load-countries"
            )

    async def _load_countries_async(self) -> None:
        try:
            countries = await self._catalog.countries()
            self._country_model.set_countries(countries)
            self.refresh_btn.setEnabled(True)
        except CatalogError as exc:
            log.warning("browse: could not load countries: %s", exc)
            self.hint_label.setText(f"Could not load countries: {exc}")
