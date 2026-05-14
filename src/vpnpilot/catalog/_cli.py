"""`vpnpilot catalog dump` — diagnostic dump of the full server catalog."""

from __future__ import annotations

import asyncio
import json
import sys

from PyQt6.QtCore import QCoreApplication

from ..cli import ProtonCLI
from .models import CatalogError
from .service import ServerCatalog


def catalog_main(args: list[str]) -> int:
    if not args or args[0] != "dump":
        print("Usage: vpnpilot catalog dump", file=sys.stderr)
        return 2

    # QObject requires a QApplication to exist.
    _app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    cli = ProtonCLI()
    return asyncio.run(_dump(cli))


async def _dump(cli: ProtonCLI) -> int:
    catalog = ServerCatalog(cli)
    try:
        countries = await catalog.countries()
    except CatalogError as exc:
        print(f"Error: failed to load countries — {exc}", file=sys.stderr)
        print("Are you signed in? Run: protonvpn signin <email>", file=sys.stderr)
        return 1

    result_countries = []
    failures: list[str] = []

    for country in countries:
        try:
            cities = await catalog.cities_async(country.code)
            result_countries.append({
                "code": country.code,
                "name": country.name,
                "cities": [
                    {
                        "name": c.name,
                        "features": sorted(f.value for f in c.features),
                    }
                    for c in cities
                ],
            })
        except CatalogError as exc:
            failures.append(f"{country.code}: {exc}")

    print(json.dumps({"countries": result_countries}, ensure_ascii=False, indent=2))

    for failure in failures:
        print(f"Warning: {failure}", file=sys.stderr)

    return 1 if failures else 0
