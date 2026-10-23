from __future__ import annotations

import argparse
import os

from .web import run_server


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Web service that creates and updates Zabbix maps from NetBox topology data"
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host interface to listen on (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port to listen on (default: 8080)",
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("LOG_LEVEL", "DEBUG"),
        help="Logging level (default: LOG_LEVEL env or DEBUG)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run_server(host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
