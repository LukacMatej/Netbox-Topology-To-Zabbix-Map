from __future__ import annotations

import logging
import os
import sys


def configure_logging(level_name: str | None = None) -> None:
    level_text = str(level_name or os.getenv("LOG_LEVEL", "DEBUG")).strip().upper() or "DEBUG"
    level = getattr(logging, level_text, logging.DEBUG)

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )