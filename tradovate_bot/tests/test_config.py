from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models.common import Point, Region, ScreenMap
from app.models.config import (
    BotConfig,
    ConfigError,
    StrategyConfig,
    load_bot_config,
    load_screen_map,
    load_strategy_config,
    save_model_json,
)


def test_point_rejects_negative():
    with pytest.raises(Exception):
        Point(x=-1, y=0)


def test_region_properties():
    r = Region(left=10, top=20, width=30, height=40)
    assert r.right == 40
    assert r.bottom == 60
    assert r.contains_point(15, 25) is True
    assert r.contains_point(40, 25) is False  # right is exclusive


def test_screen_map_bounds_helpers():
    sm = _make_screen_map()
    assert sm.point_in_screen(sm.buy_point)
    assert sm.region_in_screen(sm.price_region)
    oob = Point(x=sm.screen_width, y=0)
    assert not sm.point_in_screen(oob)


def test_screen_map_roundtrip(tmp_path: Path):
    sm = _make_screen_map()
    path = tmp_path / "screen_map.json"
    save_model_json(sm, path)
    loaded = load_screen_map(path)
    assert loaded.monitor_index == sm.monitor_index
    assert loaded.buy_point == sm.buy_point
    assert loaded.price_region == sm.price_region


def test_screen_map_missing_field(tmp_path: Path):
    bad = _make_screen_map().model_dump()
    del bad["buy_point"]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_screen_map(path)


def test_screen_map_missing_file(tmp_path: Path):
    with pytest.raises(ConfigError):
        load_screen_map(tmp_path / "nope.json")


def test_screen_map_bad_json(tmp_path: Path):
    path = tmp_path / "x.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_screen_map(path)


def test_default_bot_config_loads():
    cfg = load_bot_config(_repo_config_dir() / "bot_config.json")
    assert isinstance(cfg, BotConfig)
    assert cfg.capture_fps_target >= 1
    assert 0.0 <= cfg.anchor_match_threshold <= 1.0
    assert "gray_only" in cfg.preprocess_recipes


def test_default_strategy_config_loads():
    cfg = load_strategy_config(_repo_config_dir() / "strategy_config.json")
    assert isinstance(cfg, StrategyConfig)
    assert cfg.tick_size == 0.25
    assert cfg.symbol == "MNQ"
    assert len(cfg.session_windows) >= 1


def test_bot_config_rejects_out_of_range(tmp_path: Path):
    bad = {
        "capture_fps_target": 0,  # invalid (ge=1)
    }
    path = tmp_path / "bot_bad.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_bot_config(path)


# -------------------------- helpers -------------------------- #

def _repo_config_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "app" / "config"


def _make_screen_map() -> ScreenMap:
    return ScreenMap(
        monitor_index=1,
        screen_width=1920,
        screen_height=1080,
        browser_name="chrome",
        tradovate_anchor_region=Region(left=20, top=20, width=220, height=60),
        tradovate_anchor_reference_path="runtime/screenshots/anchor_reference.png",
        price_region=Region(left=842, top=168, width=128, height=44),
        buy_point=Point(x=1450, y=877),
        sell_point=Point(x=1532, y=877),
        cancel_all_point=Point(x=1608, y=876),
    )
