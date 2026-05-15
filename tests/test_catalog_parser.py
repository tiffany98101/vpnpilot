"""Parser tests against captured protonvpn CLI output (docs/cli-reference.md)."""

from __future__ import annotations

from vpnpilot.catalog.models import CityFeature
from vpnpilot.catalog.parser import parse_cities, parse_countries

# ---- countries list fixtures ----------------------------------------

COUNTRIES_MANY = """\
Country                           Code
--------------------------------  ------
Afghanistan                       AF
Albania                           AL
Bosnia and Herzegovina            BA
Democratic Republic of the Congo  CD
United States                     US
"""

COUNTRIES_HEADER_ONLY = """\
Country                           Code
--------------------------------  ------
"""

COUNTRIES_EMPTY = ""


def test_parse_countries_names_and_codes():
    result = parse_countries(COUNTRIES_MANY)
    codes = [c.code for c in result]
    assert codes == ["AF", "AL", "BA", "CD", "US"]


def test_parse_countries_multi_word_name():
    result = parse_countries(COUNTRIES_MANY)
    by_code = {c.code: c for c in result}
    assert by_code["BA"].name == "Bosnia and Herzegovina"
    assert by_code["CD"].name == "Democratic Republic of the Congo"
    assert by_code["US"].name == "United States"


def test_parse_countries_empty_stdout():
    assert parse_countries(COUNTRIES_EMPTY) == []


def test_parse_countries_header_only():
    assert parse_countries(COUNTRIES_HEADER_ONLY) == []


def test_parse_countries_trailing_blank_lines():
    txt = COUNTRIES_MANY + "\n\n"
    result = parse_countries(txt)
    assert len(result) == 5


# ---- cities list fixtures -------------------------------------------

# Captured from `protonvpn cities list US`
CITIES_US = """\
Cities in United States:
City            Features
--------------  ----------
Ashburn         P2P
Atlanta         P2P, Tor
Boston          P2P
New York        P2P
Salt Lake City
San Jose        P2P
Seattle         P2P
"""

# Captured from `protonvpn cities list IS` (one city)
CITIES_ICELAND = """\
Cities in Iceland:
City       Features
---------  ----------
Reykjavik  P2P
"""

# Captured from `protonvpn cities list BR` (diacritics)
CITIES_BRAZIL = """\
Cities in Brazil:
City       Features
---------  ----------
São Paulo  P2P
"""

CITIES_HEADER_ONLY = """\
Cities in Nowhere:
City       Features
---------  ----------
"""

CITIES_EMPTY = ""


def test_parse_cities_names():
    result = parse_cities(CITIES_US, "US")
    names = [c.name for c in result]
    assert "Ashburn" in names
    assert "Salt Lake City" in names
    assert "New York" in names
    assert "Seattle" in names


def test_parse_cities_p2p_feature():
    result = parse_cities(CITIES_US, "US")
    by_name = {c.name: c for c in result}
    assert CityFeature.P2P in by_name["Ashburn"].features
    assert CityFeature.TOR not in by_name["Ashburn"].features


def test_parse_cities_tor_feature():
    result = parse_cities(CITIES_US, "US")
    by_name = {c.name: c for c in result}
    assert CityFeature.TOR in by_name["Atlanta"].features
    assert CityFeature.P2P in by_name["Atlanta"].features


def test_parse_cities_no_features():
    result = parse_cities(CITIES_US, "US")
    by_name = {c.name: c for c in result}
    assert by_name["Salt Lake City"].features == frozenset()


def test_parse_cities_country_code_attached():
    result = parse_cities(CITIES_US, "US")
    assert all(c.country_code == "US" for c in result)


def test_parse_cities_one_city():
    result = parse_cities(CITIES_ICELAND, "IS")
    assert len(result) == 1
    assert result[0].name == "Reykjavik"
    assert CityFeature.P2P in result[0].features


def test_parse_cities_diacritics():
    result = parse_cities(CITIES_BRAZIL, "BR")
    assert len(result) == 1
    assert result[0].name == "São Paulo"


def test_parse_cities_empty_stdout():
    assert parse_cities(CITIES_EMPTY, "XX") == []


def test_parse_cities_header_only():
    assert parse_cities(CITIES_HEADER_ONLY, "ZZ") == []


def test_parse_cities_unknown_feature_ignored():
    txt = """\
Cities in Testland:
City    Features
------  ----------
Alpha   P2P, Quantum
"""
    result = parse_cities(txt, "TL")
    assert len(result) == 1
    assert CityFeature.P2P in result[0].features
    # "Quantum" is unknown and should be silently ignored
    assert len(result[0].features) == 1


def test_parse_cities_truncated_mid_row():
    # Truncated output should not crash, just return what was parseable.
    txt = """\
Cities in Testland:
City    Features
------  ----------
Alpha   P2P
Be"""  # truncated mid-row
    result = parse_cities(txt, "TL")
    assert result[0].name == "Alpha"
    # "Be" has no separator so it's parsed as a single-column city
    assert result[1].name == "Be"
    assert result[1].features == frozenset()
