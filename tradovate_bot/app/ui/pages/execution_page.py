"""
Execution settings page: edits `bot_config.json`.

Fields exposed match BotConfig. Risky fields show a warning label.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
                               QSpinBox, QVBoxLayout, QWidget)

from app.models.config import BotConfig, load_bot_config, save_model_json
from app.ui.app_signals import AppSignals, emit_event
from app.ui.widgets.panel import Panel
from app.utils import paths

log = logging.getLogger(__name__)


def _hint(text: str, warn: bool = False) -> QLabel:
    lbl = QLabel(text)
    if warn:
        from app.ui.theme import ARM_ORANGE
        lbl.setStyleSheet(f"color: {ARM_ORANGE}; font-weight: 600;")
    else:
        lbl.setProperty("role", "muted")
    lbl.setWordWrap(True)
    return lbl


class ExecutionPage(QWidget):
    def __init__(self, signals: AppSignals, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.signals = signals
        self._current: Optional[BotConfig] = None
        self._dirty = False

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        root.addWidget(scroll, 1)
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setContentsMargins(0, 0, 0, 0)
        inner_lay.setSpacing(10)
        scroll.setWidget(inner)

        # Capture / OCR
        p_capture = Panel("Capture + OCR")
        f_cap = QFormLayout()
        self.ew_fps = QSpinBox(); self.ew_fps.setRange(1, 60)
        self.ew_conf = QDoubleSpinBox(); self.ew_conf.setRange(0.0, 100.0); self.ew_conf.setDecimals(1)
        self.ew_stale = QSpinBox(); self.ew_stale.setRange(100, 60000); self.ew_stale.setSingleStep(100)
        self.ew_max_jump = QDoubleSpinBox(); self.ew_max_jump.setRange(0.25, 10000.0); self.ew_max_jump.setSingleStep(0.25)
        self.ew_recipes = QLineEdit()
        self.ew_recipes.setPlaceholderText("gray_only, otsu_threshold, scaled_2x_otsu, scaled_3x_binary_close")
        f_cap.addRow("Capture FPS target", self.ew_fps)
        f_cap.addRow("Min OCR confidence", self.ew_conf)
        f_cap.addRow("Price stale threshold (ms)", self.ew_stale)
        f_cap.addRow("Max jump vs last accepted (pts)", self.ew_max_jump)
        f_cap.addRow("Preprocess recipes (comma-separated)", self.ew_recipes)
        p_capture.add(self._wrap_form(f_cap))
        inner_lay.addWidget(p_capture)

        # Click behavior
        p_click = Panel("Click behavior")
        f_click = QFormLayout()
        self.ew_move = QSpinBox(); self.ew_move.setRange(0, 5000)
        self.ew_post = QSpinBox(); self.ew_post.setRange(0, 5000)
        self.ew_max_fail = QSpinBox(); self.ew_max_fail.setRange(1, 1000)
        f_click.addRow("Move duration (ms)", self.ew_move)
        f_click.addRow("Post-click delay (ms)", self.ew_post)
        f_click.addRow("Max consecutive failures", self.ew_max_fail)
        p_click.add(self._wrap_form(f_click))
        p_click.add(_hint("These delays apply during real clicks. Too low may drop clicks; "
                          "too high slows response.", warn=False))
        inner_lay.addWidget(p_click)

        # Guard
        p_guard = Panel("Guard")
        f_guard = QFormLayout()
        self.ew_anchor = QDoubleSpinBox()
        self.ew_anchor.setRange(0.0, 1.0); self.ew_anchor.setSingleStep(0.01); self.ew_anchor.setDecimals(2)
        f_guard.addRow("Anchor match threshold (0..1)", self.ew_anchor)
        p_guard.add(self._wrap_form(f_guard))
        p_guard.add(_hint(
            "Lower = more tolerant to UI changes. 0.90 is reasonable. "
            "If drift fails too often, re-calibrate rather than lowering this.",
            warn=True,
        ))
        inner_lay.addWidget(p_guard)

        # Test / debug
        p_dbg = Panel("Test / debug")
        f_dbg = QFormLayout()
        self.ew_paper_default = QCheckBox("Start in PAPER mode by default")
        self.ew_save_debug = QCheckBox("Save debug images")
        self.ew_debug_interval = QSpinBox(); self.ew_debug_interval.setRange(1, 3600)
        f_dbg.addRow("", self.ew_paper_default)
        f_dbg.addRow("", self.ew_save_debug)
        f_dbg.addRow("Debug image interval (s)", self.ew_debug_interval)
        p_dbg.add(self._wrap_form(f_dbg))
        inner_lay.addWidget(p_dbg)

        inner_lay.addStretch(1)

        # bottom bar
        bar = QHBoxLayout()
        self.dirty_label = QLabel("")
        self.dirty_label.setProperty("role", "muted")
        bar.addWidget(self.dirty_label)
        bar.addStretch(1)
        self.btn_reload = QPushButton("Reload")
        self.btn_revert = QPushButton("Revert")
        self.btn_save = QPushButton("Save")
        self.btn_save.setProperty("role", "primary")
        bar.addWidget(self.btn_reload); bar.addWidget(self.btn_revert); bar.addWidget(self.btn_save)
        root.addLayout(bar)

        self.btn_reload.clicked.connect(self._reload)
        self.btn_revert.clicked.connect(self._revert)
        self.btn_save.clicked.connect(self._save)

        for w in self._all_inputs():
            self._hook_dirty(w)

        self._reload()

    # ---- plumbing ---- #

    def _wrap_form(self, f: QFormLayout) -> QWidget:
        w = QWidget(); w.setLayout(f); return w

    def _all_inputs(self) -> list[QWidget]:
        return [self.ew_fps, self.ew_conf, self.ew_stale, self.ew_max_jump,
                self.ew_recipes, self.ew_move, self.ew_post, self.ew_max_fail,
                self.ew_anchor, self.ew_paper_default, self.ew_save_debug,
                self.ew_debug_interval]

    def _hook_dirty(self, w: QWidget) -> None:
        if isinstance(w, (QSpinBox, QDoubleSpinBox)):
            w.valueChanged.connect(self._mark_dirty)
        elif isinstance(w, QLineEdit):
            w.textChanged.connect(self._mark_dirty)
        elif isinstance(w, QCheckBox):
            w.toggled.connect(self._mark_dirty)

    def _mark_dirty(self) -> None:
        self._dirty = True
        self.dirty_label.setText("● unsaved changes")

    def _clear_dirty(self) -> None:
        self._dirty = False
        self.dirty_label.setText("")

    # ---- load / save ---- #

    def _reload(self) -> None:
        try:
            cfg = load_bot_config(paths.bot_config_path())
        except Exception as e:
            QMessageBox.critical(self, "Load failed", str(e))
            return
        self._apply(cfg)
        self._current = cfg
        self._clear_dirty()

    def _revert(self) -> None:
        if self._current is None:
            self._reload(); return
        self._apply(self._current)
        self._clear_dirty()

    def _apply(self, cfg: BotConfig) -> None:
        self.ew_fps.setValue(cfg.capture_fps_target)
        self.ew_conf.setValue(cfg.min_ocr_confidence)
        self.ew_stale.setValue(cfg.price_stale_ms)
        self.ew_max_jump.setValue(cfg.max_jump_points)
        self.ew_recipes.setText(", ".join(cfg.preprocess_recipes))
        self.ew_move.setValue(cfg.click_move_duration_ms)
        self.ew_post.setValue(cfg.click_post_delay_ms)
        self.ew_max_fail.setValue(cfg.max_consecutive_failures)
        self.ew_anchor.setValue(cfg.anchor_match_threshold)
        self.ew_paper_default.setChecked(cfg.paper_mode_default)
        self.ew_save_debug.setChecked(cfg.save_debug_images)
        self.ew_debug_interval.setValue(cfg.debug_image_interval_sec)

    def _collect(self) -> Optional[BotConfig]:
        try:
            recipes = [r.strip() for r in self.ew_recipes.text().split(",") if r.strip()]
            if not recipes:
                raise ValueError("at least one preprocess recipe required")
            return BotConfig(
                capture_fps_target=self.ew_fps.value(),
                ocr_backend="tesseract",
                min_ocr_confidence=self.ew_conf.value(),
                price_stale_ms=self.ew_stale.value(),
                anchor_match_threshold=self.ew_anchor.value(),
                click_move_duration_ms=self.ew_move.value(),
                click_post_delay_ms=self.ew_post.value(),
                max_consecutive_failures=self.ew_max_fail.value(),
                paper_mode_default=self.ew_paper_default.isChecked(),
                save_debug_images=self.ew_save_debug.isChecked(),
                debug_image_interval_sec=self.ew_debug_interval.value(),
                max_jump_points=self.ew_max_jump.value(),
                preprocess_recipes=recipes,
            )
        except Exception as e:
            QMessageBox.warning(self, "Invalid", str(e))
            return None

    def _save(self) -> None:
        cfg = self._collect()
        if cfg is None:
            return
        save_model_json(cfg, paths.bot_config_path())
        self._current = cfg
        self._clear_dirty()
        emit_event(self.signals, "info", "execution", "bot_config.json saved")
        QMessageBox.information(self, "Saved", f"Saved to {paths.bot_config_path()}")
