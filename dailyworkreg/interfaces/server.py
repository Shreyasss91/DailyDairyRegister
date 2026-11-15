"""Start the local register tool.

Bound to the loopback address on purpose: this is one operator's desk tool and it has
no authentication, so it must not be reachable from anywhere else. There is no host
option to set, because the only useful value is the one already here.
"""

from __future__ import annotations

import argparse

import uvicorn

from .api import create_app

HOST = "127.0.0.1"
DEFAULT_PORT = 8010


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dailyworkreg",
        description="Generate allocator-backed draft registers for the office.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"port to listen on at {HOST} (default {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--db",
        default=None,
        help=(
            "database file to use instead of the standard data folder "
            "(the DAILYWORKREG_DB setting is honoured too)"
        ),
    )
    parser.add_argument(
        "--print-db",
        action="store_true",
        help="print the database location and exit",
    )
    arguments = parser.parse_args(argv)

    if arguments.print_db:
        from ..infrastructure.sqlite.database import resolve_db_path

        print(resolve_db_path(arguments.db))
        return 0

    uvicorn.run(
        create_app(arguments.db),
        host=HOST,
        port=arguments.port,
        log_level="info",
        access_log=False,
    )
    return 0
