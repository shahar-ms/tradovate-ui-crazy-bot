"""
Simple terminal command reader. Pushes RuntimeCommand objects onto the
supervisor's command queue.

Commands:
  arm, disarm, halt, resume, pause, cancel_all, status, quit
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from .runtime_models import CommandName

if TYPE_CHECKING:
    from .supervisor import Supervisor

log = logging.getLogger(__name__)

VALID_COMMANDS: tuple[CommandName, ...] = (
    "arm", "disarm", "halt", "resume", "pause", "cancel_all", "status", "quit",
)


def _print_help() -> None:
    log.info("Commands: %s", ", ".join(VALID_COMMANDS))


def run_terminal_command_reader(supervisor: "Supervisor") -> threading.Thread:
    """Spawn a daemon thread that reads stdin and submits commands."""
    def _loop() -> None:
        _print_help()
        try:
            while True:
                try:
                    raw = input("> ").strip().lower()
                except EOFError:
                    return
                if not raw:
                    continue
                if raw == "help":
                    _print_help()
                    continue
                if raw not in VALID_COMMANDS:
                    log.info("Unknown command: %r. Type 'help'.", raw)
                    continue
                supervisor.submit_command(raw)  # type: ignore[arg-type]
                if raw == "quit":
                    return
        except Exception:
            log.exception("command reader crashed")

    t = threading.Thread(target=_loop, name="cmd-reader", daemon=True)
    t.start()
    return t
