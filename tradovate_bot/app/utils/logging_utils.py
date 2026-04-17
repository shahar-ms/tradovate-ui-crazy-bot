from __future__ import annotations

import logging
import sys
from pathlib import Path

from .paths import logs_dir
from .time_utils import session_id

_CONFIGURED = False
_SESSION_ID: str | None = None


def get_session_id() -> str:
    global _SESSION_ID
    if _SESSION_ID is None:
        _SESSION_ID = session_id()
    return _SESSION_ID


def setup_logging(level: str = "INFO", log_filename: str | None = None) -> logging.Logger:
    global _CONFIGURED
    root = logging.getLogger()

    if _CONFIGURED:
        return root

    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-5s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(stream=sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    name = log_filename or f"session_{get_session_id()}.log"
    log_path: Path = logs_dir() / name
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    _CONFIGURED = True
    root.info("Logging initialized (session=%s, file=%s)", get_session_id(), log_path)
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
