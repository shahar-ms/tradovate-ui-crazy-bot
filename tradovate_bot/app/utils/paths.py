from __future__ import annotations

from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def app_dir() -> Path:
    return project_root() / "app"


def config_dir() -> Path:
    return app_dir() / "config"


def runtime_dir() -> Path:
    return project_root() / "runtime"


def logs_dir() -> Path:
    d = runtime_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def screenshots_dir() -> Path:
    d = runtime_dir() / "screenshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sessions_dir() -> Path:
    d = runtime_dir() / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_dir() -> Path:
    d = runtime_dir() / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def bot_config_path() -> Path:
    return config_dir() / "bot_config.json"


def strategy_config_path() -> Path:
    return config_dir() / "strategy_config.json"


def screen_map_path() -> Path:
    return config_dir() / "screen_map.json"


def anchor_reference_path() -> Path:
    return screenshots_dir() / "anchor_reference.png"


def calibration_full_path() -> Path:
    return screenshots_dir() / "calibration_full.png"


def calibration_overlay_path() -> Path:
    return screenshots_dir() / "calibration_overlay.png"


def resolve_relative(path_str: str) -> Path:
    p = Path(path_str)
    return p if p.is_absolute() else (project_root() / p)
