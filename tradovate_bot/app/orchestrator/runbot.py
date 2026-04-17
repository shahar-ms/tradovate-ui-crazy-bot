"""
Top-level entry point. Boots the full bot and runs the supervisor loop.

Usage:
    python -m app.orchestrator.runbot                 # PRICE_DEBUG, dry-run
    python -m app.orchestrator.runbot --mode PAPER    # strategy runs, no clicks
    python -m app.orchestrator.runbot --mode ARMED    # !!! live clicks !!!
    python -m app.orchestrator.runbot --skip-calibration-check
"""

from __future__ import annotations

import argparse
import logging
import sys

from .bootstrap import BootstrapError, bootstrap
from .commands import run_terminal_command_reader
from .runtime_models import RuntimeMode
from .supervisor import Supervisor, SupervisorDeps

log = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["PRICE_DEBUG", "PAPER", "ARMED"],
                   default="PRICE_DEBUG")
    p.add_argument("--skip-calibration-check", action="store_true",
                   help="Use cached screen_map without running live anchor check.")
    p.add_argument("--no-terminal", action="store_true",
                   help="Do not start the stdin command reader.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    armed = args.mode == "ARMED"
    mode: RuntimeMode = args.mode  # type: ignore[assignment]

    try:
        br = bootstrap(initial_mode=mode, armed=armed,
                       skip_calibration_check=args.skip_calibration_check)
    except BootstrapError as e:
        print(f"bootstrap failed: {e}", file=sys.stderr)
        return 2

    deps = SupervisorDeps(
        bot_cfg=br.bot_cfg,
        screen_map=br.screen_map,
        executor=br.executor,
        engine=br.engine,
    )
    supervisor = Supervisor(deps=deps, state=br.starting_state)
    supervisor.start()

    if not args.no_terminal:
        run_terminal_command_reader(supervisor)

    try:
        supervisor.main_loop()
    finally:
        supervisor.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
