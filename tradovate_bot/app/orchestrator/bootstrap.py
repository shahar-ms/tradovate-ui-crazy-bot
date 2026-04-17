"""
Bootstrap: load configs, validate calibration, build components. Always
starts in a safe mode (PRICE_DEBUG or PAPER). Never auto-ARMs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from app.calibration.validator import validate_calibration
from app.execution.executor import Executor
from app.execution.models import ExecutionConfig, Hotkeys
from app.models.common import ScreenMap
from app.models.config import BotConfig, StrategyConfig, load_bot_config, load_screen_map, load_strategy_config
from app.strategy.engine import StrategyEngine
from app.utils import paths
from app.utils.logging_utils import get_session_id, setup_logging

from .runtime_models import RuntimeMode, RuntimeState

log = logging.getLogger(__name__)


class BootstrapError(RuntimeError):
    pass


@dataclass
class BootstrapResult:
    bot_cfg: BotConfig
    strategy_cfg: StrategyConfig
    screen_map: ScreenMap
    executor: Executor
    engine: StrategyEngine
    starting_state: RuntimeState


def bootstrap(
    initial_mode: RuntimeMode = "PRICE_DEBUG",
    armed: bool = False,
    skip_calibration_check: bool = False,
) -> BootstrapResult:
    setup_logging()
    log.info("--- bootstrap start (mode=%s, armed=%s) ---", initial_mode, armed)

    # load configs
    try:
        bot_cfg = load_bot_config(paths.bot_config_path())
        strategy_cfg = load_strategy_config(paths.strategy_config_path())
    except Exception as e:
        raise BootstrapError(f"config_load_failed: {e}") from e

    # calibration
    if not skip_calibration_check:
        report = validate_calibration(offline=False)
        for line in report.lines:
            log.info(line)
        if not report.ready:
            raise BootstrapError("calibration_invalid; run python -m app.calibration.calibrator")
    try:
        screen_map = load_screen_map(paths.screen_map_path())
    except Exception as e:
        raise BootstrapError(f"screen_map_load_failed: {e}") from e

    # executor
    dry_run = not armed
    exec_cfg = ExecutionConfig(
        move_duration_ms=bot_cfg.click_move_duration_ms,
        post_click_delay_ms=bot_cfg.click_post_delay_ms,
        anchor_match_threshold=bot_cfg.anchor_match_threshold,
        dry_run=dry_run,
        ack_evidence_save=bot_cfg.save_debug_images,
        hotkeys=Hotkeys(),
    )
    executor = Executor(screen_map=screen_map, config=exec_cfg)

    # strategy engine
    engine = StrategyEngine(strategy_cfg)

    starting = RuntimeState(
        mode=initial_mode,
        session_id=get_session_id(),
        armed=armed,
        halted=False,
    )

    log.info("--- bootstrap ok (session=%s) ---", starting.session_id)
    return BootstrapResult(
        bot_cfg=bot_cfg,
        strategy_cfg=strategy_cfg,
        screen_map=screen_map,
        executor=executor,
        engine=engine,
        starting_state=starting,
    )
