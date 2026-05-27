"""Pure functions for parsing protonvpn countries/cities CLI output.

Format captured in docs/cli-reference.md "Catalog surface" section.
No subprocess calls here — tested against inline fixtures.
"""

from __future__ import annotations

import contextlib
import re

from .models import City, CityFeature, Country

# Split on 2+ whitespace to separate columns in the tabular output.
_COL_SEP = re.compile(r"\s{2,}")
_KNOWN_FEATURE_VALUES = frozenset(feature.value for feature in CityFeature)


def parse_countries(stdout: str) -> list[Country]:
    """Parse `protonvpn countries list` stdout into Country objects.

    Skips the header and separator lines; handles multi-word country names.
    Returns an empty list on empty or unparseable input (auth errors land
    in stderr, not stdout, so empty stdout here means no countries).
    """
    countries: list[Country] = []
    in_data = False
    for line in stdout.splitlines():
        if not in_data:
            if line.startswith("---"):
                in_data = True
            continue
        stripped = line.strip()
        if not stripped:
            continue
        try:
            name, code = stripped.rsplit(maxsplit=1)
        except ValueError:
            continue
        countries.append(Country(code=code, name=name.rstrip()))
    return countries


def parse_cities(stdout: str, country_code: str) -> list[City]:
    """Parse `protonvpn cities list <CODE>` stdout into City objects.

    The Features column is optional: cities with no special features have
    nothing after the city name. Feature values are comma-separated;
    unknown future values are silently ignored.
    """
    cities: list[City] = []
    in_data = False
    for line in stdout.splitlines():
        if not in_data:
            if line.startswith("---"):
                in_data = True
            continue
        stripped = line.strip()
        if not stripped:
            continue
        parts = _COL_SEP.split(stripped)
        name = stripped
        features: frozenset[CityFeature] = frozenset()
        if len(parts) >= 2 and _is_feature_column(parts[-1]):
            name = stripped[: -len(parts[-1])].rstrip()
            features = _parse_features(parts[-1])
        cities.append(City(name=name, country_code=country_code, features=features))
    return cities


def _is_feature_column(features_str: str) -> bool:
    tokens = [token.strip() for token in features_str.split(",")]
    return bool(tokens) and all(token in _KNOWN_FEATURE_VALUES for token in tokens)


def _parse_features(features_str: str) -> frozenset[CityFeature]:
    result: set[CityFeature] = set()
    for token in features_str.split(","):
        token = token.strip()
        with contextlib.suppress(ValueError):
            result.add(CityFeature(token))
    return frozenset(result)
