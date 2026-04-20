from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from .common import ScreenMap


class BotConfig(BaseModel):
    capture_fps_target: int = Field(15, ge=1, le=60)
    ocr_backend: Literal["tesseract"] = "tesseract"
    min_ocr_confidence: float = Field(70.0, ge=0.0, le=100.0)
    price_stale_ms: int = Field(1500, gt=0)
    anchor_match_threshold: float = Field(0.90, ge=0.0, le=1.0)
    click_move_duration_ms: int = Field(80, ge=0)
    click_post_delay_ms: int = Field(120, ge=0)
    max_consecutive_failures: int = Field(10, ge=1)
    paper_mode_default: bool = True
    save_debug_images: bool = True
    debug_image_interval_sec: int = Field(10, ge=1)
    max_jump_points: float = Field(30.0, gt=0.0)
    preprocess_recipes: list[str] = Field(
        default_factory=lambda: [
            "gray_only",
            "otsu_threshold",
            "scaled_2x_otsu",
            "scaled_3x_binary_close",
        ]
    )


class SessionWindow(BaseModel):
    start: str
    end: str
    timezone: str = "Asia/Nicosia"


class StrategyConfig(BaseModel):
    symbol: str = "MNQ"
    tick_size: float = Field(0.25, gt=0.0)
    bar_seconds: int = Field(1, ge=1)
    level_lookback_bars: int = Field(120, ge=10)
    level_touch_tolerance_points: float = Field(0.5, ge=0.0)
    min_touches_for_level: int = Field(2, ge=1)
    sweep_break_distance_points: float = Field(1.0, ge=0.0)
    sweep_return_timeout_bars: int = Field(5, ge=1)
    entry_offset_points: float = 0.0
    stop_loss_points: float = Field(5.0, gt=0.0)
    take_profit_points: float = Field(12.0, gt=0.0)
    time_stop_bars: int = Field(20, ge=1)
    cooldown_bars_after_exit: int = Field(10, ge=0)
    max_trades_per_session: int = Field(6, ge=1)
    max_consecutive_losses: int = Field(2, ge=1)
    cancel_all_before_new_entry: bool = True
    session_windows: list[SessionWindow] = Field(
        default_factory=lambda: [SessionWindow(start="16:30", end="18:30")]
    )


class ConfigError(RuntimeError):
    """Raised when a config file is missing, malformed, or fails validation."""


def _load_json(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in {path}: {e}") from e


def _parse(model_cls, data: dict, source: Path):
    try:
        return model_cls.model_validate(data)
    except ValidationError as e:
        raise ConfigError(f"Invalid {model_cls.__name__} in {source}:\n{e}") from e


def load_bot_config(path: Path) -> BotConfig:
    return _parse(BotConfig, _load_json(path), path)


def load_strategy_config(path: Path) -> StrategyConfig:
    return _parse(StrategyConfig, _load_json(path), path)


def load_screen_map(path: Path) -> ScreenMap:
    return _parse(ScreenMap, _load_json(path), path)


def save_model_json(model: BaseModel, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(model.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
