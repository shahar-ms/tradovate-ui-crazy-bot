from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.calibration.validator import validate_calibration
from app.models.common import Point, Region, ScreenMap
from app.models.config import save_model_json
from app.utils import paths


@pytest.fixture
def synthetic_calibration(monkeypatch, tmp_path):
    """
    Build a fake calibration layout in tmp_path so the validator runs in offline mode
    without touching the real runtime/ folder.
    """
    # Redirect paths.* to tmp_path
    (tmp_path / "app" / "config").mkdir(parents=True)
    (tmp_path / "runtime" / "screenshots").mkdir(parents=True)
    (tmp_path / "runtime" / "logs").mkdir(parents=True)

    monkeypatch.setattr(paths, "project_root", lambda: tmp_path)
    monkeypatch.setattr(paths, "app_dir", lambda: tmp_path / "app")
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path / "app" / "config")
    monkeypatch.setattr(paths, "runtime_dir", lambda: tmp_path / "runtime")
    monkeypatch.setattr(paths, "screenshots_dir", lambda: tmp_path / "runtime" / "screenshots")
    monkeypatch.setattr(paths, "logs_dir", lambda: tmp_path / "runtime" / "logs")
    monkeypatch.setattr(paths, "bot_config_path", lambda: tmp_path / "app" / "config" / "bot_config.json")
    monkeypatch.setattr(paths, "screen_map_path", lambda: tmp_path / "app" / "config" / "screen_map.json")
    monkeypatch.setattr(paths, "anchor_reference_path",
                        lambda: tmp_path / "runtime" / "screenshots" / "anchor_reference.png")
    monkeypatch.setattr(paths, "calibration_full_path",
                        lambda: tmp_path / "runtime" / "screenshots" / "calibration_full.png")
    monkeypatch.setattr(paths, "calibration_overlay_path",
                        lambda: tmp_path / "runtime" / "screenshots" / "calibration_overlay.png")

    def resolve_rel(s: str) -> Path:
        p = Path(s)
        return p if p.is_absolute() else tmp_path / p

    monkeypatch.setattr(paths, "resolve_relative", resolve_rel)

    # Seed a bot_config.json (required by validator)
    bot_cfg = {
        "capture_fps_target": 8,
        "ocr_backend": "tesseract",
        "min_ocr_confidence": 70.0,
        "price_stale_ms": 1500,
        "anchor_match_threshold": 0.90,
        "click_move_duration_ms": 80,
        "click_post_delay_ms": 120,
        "max_consecutive_failures": 10,
        "paper_mode_default": True,
        "save_debug_images": True,
        "debug_image_interval_sec": 10,
        "max_jump_points": 30.0,
        "preprocess_recipes": ["gray_only"],
    }
    (tmp_path / "app" / "config" / "bot_config.json").write_text(
        json.dumps(bot_cfg), encoding="utf-8"
    )

    # Seed a synthetic 800x600 screenshot and anchor crop
    full = np.full((600, 800, 3), 40, dtype=np.uint8)
    # give the anchor area a unique pattern so similarity == 1.0 in offline mode (same image)
    cv2.rectangle(full, (20, 20), (240, 80), (200, 180, 100), -1)
    cv2.putText(full, "ACCOUNT", (30, 55),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2, cv2.LINE_AA)
    cv2.imwrite(str(tmp_path / "runtime" / "screenshots" / "calibration_full.png"), full)

    anchor_crop = full[20:80, 20:240].copy()
    cv2.imwrite(str(tmp_path / "runtime" / "screenshots" / "anchor_reference.png"), anchor_crop)

    sm = ScreenMap(
        monitor_index=1,
        screen_width=800,
        screen_height=600,
        browser_name="chrome",
        tradovate_anchor_region=Region(left=20, top=20, width=220, height=60),
        tradovate_anchor_reference_path="runtime/screenshots/anchor_reference.png",
        price_region=Region(left=300, top=100, width=120, height=40),
        buy_point=Point(x=500, y=400),
        sell_point=Point(x=560, y=400),
        cancel_all_point=Point(x=620, y=400),
    )
    save_model_json(sm, tmp_path / "app" / "config" / "screen_map.json")

    return tmp_path


def test_validator_offline_passes(synthetic_calibration):
    report = validate_calibration(offline=True)
    assert report.ready, "\n".join(report.lines)
    assert any("READY_FOR_FILE_02 = true" in line for line in report.lines)


def test_validator_detects_out_of_bounds_point(synthetic_calibration, monkeypatch):
    # corrupt the buy_point to be out of bounds
    sm_path = paths.screen_map_path()
    data = json.loads(sm_path.read_text())
    data["buy_point"] = {"x": 9999, "y": 9999}
    sm_path.write_text(json.dumps(data), encoding="utf-8")

    report = validate_calibration(offline=True)
    assert not report.ready
    assert any("buy_point out of bounds" in line for line in report.lines)


def test_validator_missing_anchor_ref(synthetic_calibration):
    (paths.anchor_reference_path()).unlink()
    report = validate_calibration(offline=True)
    assert not report.ready
    assert any("anchor reference image not found" in line for line in report.lines)


def test_validator_missing_screen_map(synthetic_calibration):
    paths.screen_map_path().unlink()
    report = validate_calibration(offline=True)
    assert not report.ready
    assert any("screen_map.json not found" in line for line in report.lines)
