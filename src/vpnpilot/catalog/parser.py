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
        parts = _COL_SEP.split(stripped)
        if len(parts) >= 2:
            # Last token is always the 2-letter code; everything before is the name.
            countries.append(Country(code=parts[-1], name=" ".join(parts[:-1])))
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
        name = parts[0]
        features: frozenset[CityFeature] = frozenset()
        if len(parts) >= 2:
            features = _parse_features(parts[1])
        cities.append(City(name=name, country_code=country_code, features=features))
    return cities


def _parse_features(features_str: str) -> frozenset[CityFeature]:
    result: set[CityFeature] = set()
    for token in features_str.split(","):
        token = token.strip()
        with contextlib.suppress(ValueError):
            result.add(CityFeature(token))
    return frozenset(result)
