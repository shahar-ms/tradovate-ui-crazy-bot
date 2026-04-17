"""
Strategy settings page. Loads / edits / saves `strategy_config.json`.

Only exposes fields that actually exist on StrategyConfig to avoid the UI
drifting from the schema.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
                               QSpinBox, QVBoxLayout, QWidget)

from app.models.config import (SessionWindow, StrategyConfig, load_strategy_config,
                               save_model_json)
from app.ui.app_signals import AppSignals, emit_event
from app.ui.widgets.panel import Panel
from app.utils import paths

log = logging.getLogger(__name__)


def _hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("role", "muted")
    lbl.setWordWrap(True)
    return lbl


class StrategyPage(QWidget):
    def __init__(self, signals: AppSignals, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.signals = signals
        self._current: Optional[StrategyConfig] = None
        self._dirty = False

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(8)

        # scroll area because there are many fields
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        root.addWidget(scroll, 1)

        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setContentsMargins(0, 0, 0, 0)
        inner_lay.setSpacing(10)
        scroll.setWidget(inner)

        # entry model
        p_entry = Panel("Entry model")
        f_entry = QFormLayout()
        self.sw_symbol = QLineEdit()
        self.sw_tick_size = QDoubleSpinBox(); self.sw_tick_size.setRange(0.01, 100.0); self.sw_tick_size.setSingleStep(0.05); self.sw_tick_size.setDecimals(4)
        self.sw_bar_seconds = QSpinBox(); self.sw_bar_seconds.setRange(1, 3600)
        self.sw_lookback = QSpinBox(); self.sw_lookback.setRange(10, 10000)
        self.sw_touch_tol = QDoubleSpinBox(); self.sw_touch_tol.setRange(0.0, 100.0); self.sw_touch_tol.setSingleStep(0.25); self.sw_touch_tol.setDecimals(2)
        self.sw_min_touches = QSpinBox(); self.sw_min_touches.setRange(1, 20)
        self.sw_break_dist = QDoubleSpinBox(); self.sw_break_dist.setRange(0.0, 500.0); self.sw_break_dist.setSingleStep(0.25)
        self.sw_return_timeout = QSpinBox(); self.sw_return_timeout.setRange(1, 500)
        self.sw_entry_offset = QDoubleSpinBox(); self.sw_entry_offset.setRange(-100.0, 100.0); self.sw_entry_offset.setSingleStep(0.25)
        f_entry.addRow("Symbol", self.sw_symbol)
        f_entry.addRow("Tick size (points)", self.sw_tick_size)
        f_entry.addRow("Bar seconds", self.sw_bar_seconds)
        f_entry.addRow("Level lookback (bars)", self.sw_lookback)
        f_entry.addRow("Level touch tolerance (pts)", self.sw_touch_tol)
        f_entry.addRow("Min touches for level", self.sw_min_touches)
        f_entry.addRow("Sweep break distance (pts)", self.sw_break_dist)
        f_entry.addRow("Sweep return timeout (bars)", self.sw_return_timeout)
        f_entry.addRow("Entry offset (pts)", self.sw_entry_offset)
        p_entry.add(self._wrap_form(f_entry))
        p_entry.add(_hint("A valid level needs at least 'min touches'. A sweep is a push "
                          "through the level by 'break distance', followed by a close back "
                          "through it within the timeout."))
        inner_lay.addWidget(p_entry)

        # stops and targets
        p_exits = Panel("Stop / target / time stop")
        f_exits = QFormLayout()
        self.sw_sl = QDoubleSpinBox(); self.sw_sl.setRange(0.25, 10000.0); self.sw_sl.setSingleStep(0.25)
        self.sw_tp = QDoubleSpinBox(); self.sw_tp.setRange(0.25, 10000.0); self.sw_tp.setSingleStep(0.25)
        self.sw_time_stop = QSpinBox(); self.sw_time_stop.setRange(1, 10000)
        f_exits.addRow("Stop loss (pts)", self.sw_sl)
        f_exits.addRow("Take profit (pts)", self.sw_tp)
        f_exits.addRow("Time stop (bars)", self.sw_time_stop)
        p_exits.add(self._wrap_form(f_exits))
        inner_lay.addWidget(p_exits)

        # time and safety
        p_safety = Panel("Session + safety")
        f_safety = QFormLayout()
        self.sw_cooldown = QSpinBox(); self.sw_cooldown.setRange(0, 10000)
        self.sw_max_trades = QSpinBox(); self.sw_max_trades.setRange(1, 100)
        self.sw_max_losses = QSpinBox(); self.sw_max_losses.setRange(1, 100)
        self.sw_cancel_before = QCheckBox("Cancel-all before every new entry")
        self.sw_sessions = QLineEdit()
        self.sw_sessions.setPlaceholderText('[{"start":"16:30","end":"18:30","timezone":"Asia/Nicosia"}]')
        f_safety.addRow("Cooldown after exit (bars)", self.sw_cooldown)
        f_safety.addRow("Max trades per session", self.sw_max_trades)
        f_safety.addRow("Max consecutive losses", self.sw_max_losses)
        f_safety.addRow("", self.sw_cancel_before)
        f_safety.addRow("Session windows (JSON)", self.sw_sessions)
        p_safety.add(self._wrap_form(f_safety))
        p_safety.add(_hint("Session windows use local times in the given timezone. "
                           "At least one window is required. JSON list form."))
        inner_lay.addWidget(p_safety)

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
        bar.addWidget(self.btn_reload)
        bar.addWidget(self.btn_revert)
        bar.addWidget(self.btn_save)
        root.addLayout(bar)

        self.btn_reload.clicked.connect(self._reload)
        self.btn_revert.clicked.connect(self._revert)
        self.btn_save.clicked.connect(self._save)

        # dirty tracking on every input
        for w in self._all_inputs():
            self._hook_dirty(w)

        self._reload()

    # ---- plumbing ---- #

    def _wrap_form(self, f: QFormLayout) -> QWidget:
        w = QWidget()
        w.setLayout(f)
        return w

    def _all_inputs(self) -> list[QWidget]:
        return [self.sw_symbol, self.sw_tick_size, self.sw_bar_seconds, self.sw_lookback,
                self.sw_touch_tol, self.sw_min_touches, self.sw_break_dist,
                self.sw_return_timeout, self.sw_entry_offset, self.sw_sl, self.sw_tp,
                self.sw_time_stop, self.sw_cooldown, self.sw_max_trades,
                self.sw_max_losses, self.sw_cancel_before, self.sw_sessions]

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
            cfg = load_strategy_config(paths.strategy_config_path())
        except Exception as e:
            QMessageBox.critical(self, "Load failed", str(e))
            return
        self._apply(cfg)
        self._current = cfg
        self._clear_dirty()

    def _revert(self) -> None:
        if self._current is None:
            self._reload()
            return
        self._apply(self._current)
        self._clear_dirty()

    def _apply(self, cfg: StrategyConfig) -> None:
        self.sw_symbol.setText(cfg.symbol)
        self.sw_tick_size.setValue(cfg.tick_size)
        self.sw_bar_seconds.setValue(cfg.bar_seconds)
        self.sw_lookback.setValue(cfg.level_lookback_bars)
        self.sw_touch_tol.setValue(cfg.level_touch_tolerance_points)
        self.sw_min_touches.setValue(cfg.min_touches_for_level)
        self.sw_break_dist.setValue(cfg.sweep_break_distance_points)
        self.sw_return_timeout.setValue(cfg.sweep_return_timeout_bars)
        self.sw_entry_offset.setValue(cfg.entry_offset_points)
        self.sw_sl.setValue(cfg.stop_loss_points)
        self.sw_tp.setValue(cfg.take_profit_points)
        self.sw_time_stop.setValue(cfg.time_stop_bars)
        self.sw_cooldown.setValue(cfg.cooldown_bars_after_exit)
        self.sw_max_trades.setValue(cfg.max_trades_per_session)
        self.sw_max_losses.setValue(cfg.max_consecutive_losses)
        self.sw_cancel_before.setChecked(cfg.cancel_all_before_new_entry)
        self.sw_sessions.setText(
            json.dumps([w.model_dump() for w in cfg.session_windows])
        )

    def _collect(self) -> Optional[StrategyConfig]:
        try:
            windows_raw = self.sw_sessions.text().strip() or "[]"
            windows = [SessionWindow(**w) for w in json.loads(windows_raw)]
            if not windows:
                raise ValueError("at least one session window is required")
            cfg = StrategyConfig(
                symbol=self.sw_symbol.text() or "MNQ",
                tick_size=self.sw_tick_size.value(),
                bar_seconds=self.sw_bar_seconds.value(),
                level_lookback_bars=self.sw_lookback.value(),
                level_touch_tolerance_points=self.sw_touch_tol.value(),
                min_touches_for_level=self.sw_min_touches.value(),
                sweep_break_distance_points=self.sw_break_dist.value(),
                sweep_return_timeout_bars=self.sw_return_timeout.value(),
                entry_offset_points=self.sw_entry_offset.value(),
                stop_loss_points=self.sw_sl.value(),
                take_profit_points=self.sw_tp.value(),
                time_stop_bars=self.sw_time_stop.value(),
                cooldown_bars_after_exit=self.sw_cooldown.value(),
                max_trades_per_session=self.sw_max_trades.value(),
                max_consecutive_losses=self.sw_max_losses.value(),
                cancel_all_before_new_entry=self.sw_cancel_before.isChecked(),
                session_windows=windows,
            )
            return cfg
        except Exception as e:
            QMessageBox.warning(self, "Invalid", str(e))
            return None

    def _save(self) -> None:
        cfg = self._collect()
        if cfg is None:
            return
        save_model_json(cfg, paths.strategy_config_path())
        self._current = cfg
        self._clear_dirty()
        emit_event(self.signals, "info", "strategy", "strategy_config.json saved")
        QMessageBox.information(self, "Saved",
                                f"Saved to {paths.strategy_config_path()}")
