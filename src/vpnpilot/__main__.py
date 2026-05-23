from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "catalog":
        from .catalog._cli import catalog_main

        return catalog_main(sys.argv[2:])
    if len(sys.argv) >= 2 and sys.argv[1] == "backend":
        from .backend_cli import backend_main

        return backend_main(sys.argv[2:])
    from .app import main as _app_main

    return _app_main()


if __name__ == "__main__":
    raise SystemExit(main())
