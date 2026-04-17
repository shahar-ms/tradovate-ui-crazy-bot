"""
Calibration page.

Flow:
  1. pick a monitor
  2. capture current screen
  3. mark items one-by-one (anchor region, price region, buy/sell/cancel, optional)
  4. save
  5. validate

Persists via the same ScreenMap model used by the rest of the bot.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import mss
import numpy as np
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QSplitter,
                               QVBoxLayout, QWidget)

from app.calibration.validator import validate_calibration
from app.models.common import Point, Region, ScreenMap
from app.models.config import save_model_json
from app.ui.app_signals import AppSignals, emit_event
from app.ui.widgets.calibration_canvas import CalibrationCanvas, CanvasOverlay
from app.ui.widgets.labeled_value import LabeledValue
from app.ui.widgets.panel import Panel
from app.utils import image_utils as iu
from app.utils import paths

log = logging.getLogger(__name__)


@dataclass
class CalibTargets:
    anchor: Optional[Region] = None
    price: Optional[Region] = None
    buy: Optional[Point] = None
    sell: Optional[Point] = None
    cancel: Optional[Point] = None
    position: Optional[Region] = None
    status: Optional[Region] = None


ITEMS = [
    ("anchor", "region", "Anchor region",   "#e8781e", True),
    ("price",  "region", "Price region",    "#35c46a", True),
    ("buy",    "point",  "Buy button",      "#35c46a", True),
    ("sell",   "point",  "Sell button",     "#e04242", True),
    ("cancel", "point",  "Cancel-all",      "#d4a017", True),
    ("position", "region", "Position region (optional)", "#3b82f6", False),
    ("status",   "region", "Status region (optional)",   "#a855f7", False),
]


class CalibrationPage(QWidget):
    def __init__(self, signals: AppSignals, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.signals = signals
        self.targets = CalibTargets()
        self._full_image: Optional[np.ndarray] = None
        self._monitor_index: int = 1
        self._monitor_size: tuple[int, int] = (0, 0)
        self._current_item_key: Optional[str] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(8)

        # top controls
        top = QHBoxLayout()
        top.setSpacing(8)

        top.addWidget(QLabel("Monitor:"))
        self.monitor_combo = QComboBox()
        self._populate_monitors()
        top.addWidget(self.monitor_combo)

        self.btn_capture = QPushButton("Capture monitor")
        top.addWidget(self.btn_capture)

        top.addWidget(QLabel("  |  Mark:"))
        self.item_combo = QComboBox()
        for key, _kind, label, _color, required in ITEMS:
            self.item_combo.addItem(f"{label}{'' if required else ' (opt)'}", userData=key)
        top.addWidget(self.item_combo)

        self.btn_start_mark = QPushButton("Start mark")
        self.btn_commit = QPushButton("Commit (Enter)")
        self.btn_cancel_mark = QPushButton("Cancel mark (Esc)")
        self.btn_clear_item = QPushButton("Clear item")
        for b in (self.btn_start_mark, self.btn_commit, self.btn_cancel_mark, self.btn_clear_item):
            top.addWidget(b)

        top.addStretch(1)
        root.addLayout(top)

        # main body: canvas on the left, info on the right
        splitter = QSplitter(Qt.Horizontal)

        # left: canvas
        self.canvas = CalibrationCanvas()
        self.canvas.region_marked.connect(self._on_region_marked)
        self.canvas.point_marked.connect(self._on_point_marked)
        splitter.addWidget(self.canvas)

        # right column
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(8)

        status_panel = Panel("Calibration state")
        self.lv_monitor = LabeledValue("Monitor")
        self.lv_size = LabeledValue("Resolution")
        self.lv_captured = LabeledValue("Screenshot captured")
        for w in (self.lv_monitor, self.lv_size, self.lv_captured):
            status_panel.add(w)
        right_lay.addWidget(status_panel)

        items_panel = Panel("Marked items")
        self.items_list = QListWidget()
        items_panel.add(self.items_list)
        right_lay.addWidget(items_panel, 1)

        actions_panel = Panel("Actions")
        row1 = QHBoxLayout()
        self.btn_save = QPushButton("Save calibration")
        self.btn_save.setProperty("role", "primary")
        self.btn_load = QPushButton("Load saved")
        row1.addWidget(self.btn_save)
        row1.addWidget(self.btn_load)
        actions_panel.add(self._wrap_row(row1))

        row2 = QHBoxLayout()
        self.btn_validate = QPushButton("Validate (live)")
        self.btn_validate_offline = QPushButton("Validate (offline)")
        row2.addWidget(self.btn_validate)
        row2.addWidget(self.btn_validate_offline)
        actions_panel.add(self._wrap_row(row2))

        row3 = QHBoxLayout()
        self.btn_reset_all = QPushButton("Reset all")
        self.btn_reset_all.setProperty("role", "danger")
        row3.addWidget(self.btn_reset_all)
        actions_panel.add(self._wrap_row(row3))

        right_lay.addWidget(actions_panel)

        splitter.addWidget(right)
        splitter.setSizes([900, 380])
        root.addWidget(splitter, 1)

        # wiring
        self.btn_capture.clicked.connect(self._capture_monitor)
        self.btn_start_mark.clicked.connect(self._start_mark)
        self.btn_commit.clicked.connect(self.canvas.commit)
        self.btn_cancel_mark.clicked.connect(self.canvas.cancel_mark)
        self.btn_clear_item.clicked.connect(self._clear_current_item)
        self.btn_save.clicked.connect(self._save)
        self.btn_load.clicked.connect(self._load_saved)
        self.btn_validate.clicked.connect(lambda: self._validate(offline=False))
        self.btn_validate_offline.clicked.connect(lambda: self._validate(offline=True))
        self.btn_reset_all.clicked.connect(self._reset_all)

        self._refresh_items_list()
        self._refresh_status()

    # ---- helpers ---- #

    def _wrap_row(self, lay) -> QWidget:
        w = QWidget()
        w.setLayout(lay)
        return w

    def _populate_monitors(self) -> None:
        self.monitor_combo.clear()
        try:
            with mss.mss() as sct:
                for i, mon in enumerate(sct.monitors):
                    if i == 0:
                        continue  # virtual
                    self.monitor_combo.addItem(
                        f"Monitor {i}: {mon['width']}x{mon['height']} @ ({mon['left']},{mon['top']})",
                        userData=i,
                    )
        except Exception as e:
            self.monitor_combo.addItem(f"(mss error: {e})", userData=1)

    # ---- actions ---- #

    def _capture_monitor(self) -> None:
        idx = self.monitor_combo.currentData()
        if idx is None:
            QMessageBox.warning(self, "No monitor", "No monitor selected")
            return
        try:
            with mss.mss() as sct:
                mon = sct.monitors[idx]
                raw = np.array(sct.grab(mon))
                bgr = iu.bgra_to_bgr(raw)
        except Exception as e:
            QMessageBox.critical(self, "Capture failed", str(e))
            return

        self._full_image = bgr
        self._monitor_index = idx
        self._monitor_size = (int(mon["width"]), int(mon["height"]))
        self.canvas.set_image(bgr)
        self._redraw_overlays()
        self._refresh_status()
        emit_event(self.signals, "info", "calibration",
                   f"captured monitor {idx}: {self._monitor_size[0]}x{self._monitor_size[1]}")

    def _start_mark(self) -> None:
        if self._full_image is None:
            QMessageBox.warning(self, "No screenshot", "Capture the monitor first.")
            return
        key = self.item_combo.currentData()
        kind = next(it[1] for it in ITEMS if it[0] == key)
        self._current_item_key = key
        if kind == "region":
            self.canvas.start_mark_region()
        else:
            self.canvas.start_mark_point()
        self.canvas.setFocus()

    def _clear_current_item(self) -> None:
        key = self.item_combo.currentData()
        setattr(self.targets, key, None)
        self._redraw_overlays()
        self._refresh_items_list()

    @Slot(int, int, int, int)
    def _on_region_marked(self, left: int, top: int, width: int, height: int) -> None:
        if self._current_item_key is None:
            return
        setattr(self.targets, self._current_item_key,
                Region(left=left, top=top, width=width, height=height))
        self._current_item_key = None
        self._redraw_overlays()
        self._refresh_items_list()

    @Slot(int, int)
    def _on_point_marked(self, x: int, y: int) -> None:
        if self._current_item_key is None:
            return
        setattr(self.targets, self._current_item_key, Point(x=x, y=y))
        self._current_item_key = None
        self._redraw_overlays()
        self._refresh_items_list()

    # ---- overlays / display ---- #

    def _redraw_overlays(self) -> None:
        overlays: list[CanvasOverlay] = []
        for key, kind, label, color, _required in ITEMS:
            val = getattr(self.targets, key)
            if val is None:
                continue
            qcolor = QColor(color)
            if kind == "region":
                overlays.append(CanvasOverlay(kind="region", label=label, color=qcolor,
                                              left=val.left, top=val.top,
                                              width=val.width, height=val.height))
            else:
                overlays.append(CanvasOverlay(kind="point", label=label, color=qcolor,
                                              x=val.x, y=val.y))
        self.canvas.set_overlays(overlays)

    def _refresh_items_list(self) -> None:
        self.items_list.clear()
        for key, kind, label, _color, required in ITEMS:
            val = getattr(self.targets, key)
            if val is None:
                txt = f"[ ] {label}" + ("" if required else "  (optional)")
            elif kind == "region":
                txt = f"[X] {label}  {val.width}x{val.height} @ ({val.left},{val.top})"
            else:
                txt = f"[X] {label}  ({val.x}, {val.y})"
            self.items_list.addItem(QListWidgetItem(txt))

    def _refresh_status(self) -> None:
        self.lv_monitor.set_value(str(self._monitor_index))
        self.lv_size.set_value(f"{self._monitor_size[0]}x{self._monitor_size[1]}"
                               if self._monitor_size[0] else "—")
        self.lv_captured.set_value("yes" if self._full_image is not None else "no",
                                   status="ok" if self._full_image is not None else "inactive")

    # ---- save / load / validate ---- #

    def _missing_required(self) -> list[str]:
        missing: list[str] = []
        for key, _kind, label, _color, required in ITEMS:
            if required and getattr(self.targets, key) is None:
                missing.append(label)
        return missing

    def _save(self) -> None:
        if self._full_image is None:
            QMessageBox.warning(self, "No screenshot", "Capture the monitor first.")
            return
        missing = self._missing_required()
        if missing:
            QMessageBox.warning(self, "Missing items",
                                "Still need: " + ", ".join(missing))
            return

        # save anchor crop
        a = self.targets.anchor
        anchor_crop = iu.crop(self._full_image, a.left, a.top, a.width, a.height)
        iu.save_png(anchor_crop, paths.anchor_reference_path())
        iu.save_png(self._full_image, paths.calibration_full_path())

        # save overlay preview
        preview = self._full_image.copy()
        for key, kind, label, color_hex, _req in ITEMS:
            v = getattr(self.targets, key)
            if v is None:
                continue
            c = QColor(color_hex)
            bgr = (c.blue(), c.green(), c.red())
            if kind == "region":
                iu.draw_region(preview, v.left, v.top, v.width, v.height, bgr, label)
            else:
                iu.draw_point(preview, v.x, v.y, bgr, label)
        iu.save_png(preview, paths.calibration_overlay_path())

        screen_map = ScreenMap(
            monitor_index=self._monitor_index,
            screen_width=self._monitor_size[0],
            screen_height=self._monitor_size[1],
            browser_name="chrome",
            tradovate_anchor_region=self.targets.anchor,
            tradovate_anchor_reference_path=str(
                paths.anchor_reference_path().relative_to(paths.project_root())
            ).replace("\\", "/"),
            price_region=self.targets.price,
            buy_point=self.targets.buy,
            sell_point=self.targets.sell,
            cancel_all_point=self.targets.cancel,
            position_region=self.targets.position,
            status_region=self.targets.status,
        )
        save_model_json(screen_map, paths.screen_map_path())
        emit_event(self.signals, "info", "calibration",
                   f"saved screen_map.json ({self._monitor_size[0]}x{self._monitor_size[1]})")
        self.signals.calibration_reloaded.emit()
        QMessageBox.information(self, "Saved",
                                f"Calibration saved to:\n{paths.screen_map_path()}")

    def _load_saved(self) -> None:
        if not paths.screen_map_path().exists():
            QMessageBox.information(self, "No calibration",
                                    f"No file at {paths.screen_map_path()}")
            return
        try:
            from app.models.config import load_screen_map
            sm = load_screen_map(paths.screen_map_path())
        except Exception as e:
            QMessageBox.critical(self, "Load failed", str(e))
            return

        self._monitor_index = sm.monitor_index
        self._monitor_size = (sm.screen_width, sm.screen_height)
        self.targets = CalibTargets(
            anchor=sm.tradovate_anchor_region,
            price=sm.price_region,
            buy=sm.buy_point,
            sell=sm.sell_point,
            cancel=sm.cancel_all_point,
            position=sm.position_region,
            status=sm.status_region,
        )

        # load full screenshot if exists
        full_path = paths.calibration_full_path()
        if full_path.exists():
            self._full_image = iu.load_png(full_path)
            self.canvas.set_image(self._full_image)
        else:
            self.canvas.clear_image()

        self._redraw_overlays()
        self._refresh_items_list()
        self._refresh_status()
        QMessageBox.information(self, "Loaded",
                                f"Loaded from {paths.screen_map_path()}")

    def _validate(self, offline: bool) -> None:
        report = validate_calibration(offline=offline)
        text = "\n".join(report.lines)
        if report.ready:
            QMessageBox.information(self, "Validation passed", text)
        else:
            QMessageBox.critical(self, "Validation failed", text)

    def _reset_all(self) -> None:
        if QMessageBox.question(self, "Reset calibration?",
                                "Clear all marked items in this editor? "
                                "(the saved JSON file is not deleted)",
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self.targets = CalibTargets()
        self._redraw_overlays()
        self._refresh_items_list()
