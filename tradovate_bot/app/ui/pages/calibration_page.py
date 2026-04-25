"""
Calibration page.

Flow:
  1. load a screenshot (capture this machine's monitor OR load from file)
  2. pick the target monitor (where the bot will click at runtime)
  3. mark items one-by-one (anchor region, price region, buy/sell/cancel, optional)
  4. save
  5. validate

Persists via the same ScreenMap model used by the rest of the bot.

Note on the two sources:
  - "Capture this machine's screen" grabs the CURRENT monitor of the PC
    running the UI. Use this when the bot and Tradovate run on the same PC.
  - "Load screenshot from file" loads a PNG/JPG captured elsewhere (for
    example: Tradovate runs on a different machine). At runtime the bot
    still captures `monitor_index` on THIS machine — so the image
    dimensions must match that monitor's resolution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import cv2
import mss
import numpy as np
from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QSplitter,
                               QVBoxLayout, QWidget)

from app.calibration.validator import validate_calibration
from app.models.common import Point, Region, ScreenMap
from app.models.config import save_model_json
from app.ui.app_signals import AppSignals, emit_event
from app.ui.dialogs.window_picker_dialog import WindowPickerDialog
from app.ui.widgets.calibration_canvas import CalibrationCanvas, CanvasOverlay
from app.ui.widgets.labeled_value import LabeledValue
from app.ui.widgets.panel import Panel
from app.utils import image_utils as iu
from app.utils import paths

COUNTDOWN_SECONDS = 3

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
    # Signed-integer position-size cell. Sign gives side: +1 = long, -1 =
    # short, 0 = flat. Source of truth for FLAT <-> in-position transitions.
    position_size: Optional[Region] = None
    # Entry-price cell (verified broker average fill while in a position).
    # Paired with position_size: together they give the HUD everything
    # needed for live PnL without AckReader's fill-price OCR.
    entry_price: Optional[Region] = None


ITEMS = [
    # Only the anchor (drift detection) and Cancel-All (safety: always
    # clickable) are required. Everything else is optional so the operator
    # can stage calibration incrementally — calibrate the anchor once,
    # use the bot, then add price/buy/sell as needed.
    ("anchor", "region", "Anchor region",   "#e8781e", True),
    ("price",  "region", "Price region",    "#35c46a", True),
    ("cancel", "point",  "Cancel-all",      "#d4a017", True),
    ("buy",    "point",  "Buy button (optional)",      "#35c46a", False),
    ("sell",   "point",  "Sell button (optional)",     "#e04242", False),
    ("position_size", "region",
     "Position SIZE region (optional; signed integer; +N=long, -N=short, 0=flat)",
     "#ff7f50", False),
    ("entry_price", "region",
     "Entry PRICE region (optional; broker's verified avg fill — drives PnL)",
     "#22c55e", False),
    ("position", "region", "Position region (optional)", "#3b82f6", False),
    ("status",   "region", "Status region (optional)",   "#a855f7", False),
]


class CalibrationPage(QWidget):
    def __init__(self, signals: AppSignals, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.signals = signals
        self.targets = CalibTargets()
        self._full_image: Optional[np.ndarray] = None
        self._image_source: str = "none"   # "capture" | "file:<path>" | "none"
        self._monitor_index: int = 1
        self._monitor_size: tuple[int, int] = (0, 0)
        self._current_item_key: Optional[str] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(8)

        # --- row 1: screenshot source --- #
        src_row = QHBoxLayout()
        src_row.setSpacing(8)

        src_row.addWidget(QLabel("Target monitor (where bot clicks at runtime):"))
        self.monitor_combo = QComboBox()
        self._populate_monitors()
        src_row.addWidget(self.monitor_combo)

        self.btn_capture = QPushButton(f"Capture screen ({COUNTDOWN_SECONDS}s delay)")
        self.btn_capture.setToolTip(
            f"Wait {COUNTDOWN_SECONDS} seconds so you can bring Tradovate to the front, "
            "then grab the full monitor of the PC running this UI."
        )
        self.btn_capture_window = QPushButton("Capture specific window…")
        self.btn_capture_window.setToolTip(
            "Pick a window by title (e.g. Tradovate tab). The UI activates it, "
            f"waits {COUNTDOWN_SECONDS} seconds, then captures the full monitor. "
            "Coordinates stay absolute so the bot can click correctly at runtime."
        )
        self.btn_load_file = QPushButton("Load screenshot from file…")
        self.btn_load_file.setToolTip(
            "Load a PNG/JPG screenshot from disk.\n"
            "Useful when Tradovate is on a different machine."
        )
        self.btn_reset_image = QPushButton("Change screenshot")
        self.btn_reset_image.setToolTip(
            "Clear the current screenshot + all marks so you can load a new one."
        )
        src_row.addWidget(self.btn_capture)
        src_row.addWidget(self.btn_capture_window)
        src_row.addWidget(self.btn_load_file)
        src_row.addWidget(self.btn_reset_image)

        src_row.addStretch(1)
        root.addLayout(src_row)

        # --- row 2: marking controls --- #
        mark_row = QHBoxLayout()
        mark_row.setSpacing(8)

        mark_row.addWidget(QLabel("Mark:"))
        self.item_combo = QComboBox()
        for key, _kind, label, _color, required in ITEMS:
            self.item_combo.addItem(f"{label}{'' if required else ' (opt)'}", userData=key)
        mark_row.addWidget(self.item_combo)

        self.btn_start_mark = QPushButton("Start mark")
        self.btn_commit = QPushButton("Commit (Enter)")
        self.btn_cancel_mark = QPushButton("Cancel mark (Esc)")
        self.btn_clear_item = QPushButton("Clear selected item")
        self.btn_clear_item.setToolTip(
            "Clears the item currently selected in the 'Marked items' list on the right, "
            "or the one chosen in the 'Mark' dropdown if no row is selected."
        )
        for b in (self.btn_start_mark, self.btn_commit, self.btn_cancel_mark, self.btn_clear_item):
            mark_row.addWidget(b)

        mark_row.addStretch(1)
        root.addLayout(mark_row)

        # --- main body: canvas on the left, info on the right --- #
        splitter = QSplitter(Qt.Horizontal)

        self.canvas = CalibrationCanvas()
        self.canvas.region_marked.connect(self._on_region_marked)
        self.canvas.point_marked.connect(self._on_point_marked)
        splitter.addWidget(self.canvas)

        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(8)

        status_panel = Panel("Calibration state")
        self.lv_source = LabeledValue("Screenshot source")
        self.lv_monitor = LabeledValue("Target monitor")
        self.lv_size = LabeledValue("Image resolution")
        for w in (self.lv_source, self.lv_monitor, self.lv_size):
            status_panel.add(w)
        right_lay.addWidget(status_panel)

        items_panel = Panel("Marked items  (click a row + 'Clear selected item' to remove)")
        self.items_list = QListWidget()
        self.items_list.itemDoubleClicked.connect(self._on_items_double_clicked)
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
        self.btn_reset_all = QPushButton("Reset all marks")
        self.btn_reset_all.setToolTip(
            "Clear every marked item in this editor. The saved JSON on disk is kept."
        )
        self.btn_delete_saved = QPushButton("Delete saved calibration")
        self.btn_delete_saved.setProperty("role", "danger")
        self.btn_delete_saved.setToolTip(
            "Delete screen_map.json and its reference images from disk. "
            "The bot will refuse to start until you calibrate again."
        )
        row3.addWidget(self.btn_reset_all)
        row3.addWidget(self.btn_delete_saved)
        actions_panel.add(self._wrap_row(row3))

        right_lay.addWidget(actions_panel)

        splitter.addWidget(right)
        splitter.setSizes([900, 380])
        root.addWidget(splitter, 1)

        # --- countdown state --- #
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._countdown_tick)
        self._countdown_remaining: int = 0
        self._countdown_default_label: str = self.btn_capture.text()
        self._countdown_callback: Optional[Callable[[], None]] = None

        # --- wiring --- #
        self.btn_capture.clicked.connect(lambda: self._start_countdown(
            on_zero=self._grab_full_monitor, label_prefix="Capturing in"))
        self.btn_capture_window.clicked.connect(self._capture_specific_window)
        self.btn_load_file.clicked.connect(self._load_from_file)
        self.btn_reset_image.clicked.connect(self._reset_image)
        self.btn_start_mark.clicked.connect(self._start_mark)
        self.btn_commit.clicked.connect(self.canvas.commit)
        self.btn_cancel_mark.clicked.connect(self.canvas.cancel_mark)
        self.btn_clear_item.clicked.connect(self._clear_current_item)
        self.btn_save.clicked.connect(self._save)
        self.btn_load.clicked.connect(self._load_saved)
        self.btn_validate.clicked.connect(lambda: self._validate(offline=False))
        self.btn_validate_offline.clicked.connect(lambda: self._validate(offline=True))
        self.btn_reset_all.clicked.connect(self._reset_all_marks)
        self.btn_delete_saved.clicked.connect(self._delete_saved_calibration)

        self._refresh_items_list()
        self._refresh_status()
        self._refresh_image_buttons()

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

    # ---- image sources ---- #

    def _start_countdown(self, on_zero: Callable[[], None],
                         label_prefix: str = "Capturing in",
                         seconds: int = COUNTDOWN_SECONDS) -> None:
        """Begin a non-blocking countdown, then call on_zero()."""
        if self.monitor_combo.currentData() is None:
            QMessageBox.warning(self, "No monitor", "No monitor selected")
            return
        if self._countdown_timer.isActive():
            return  # already counting
        self._countdown_remaining = seconds
        self._countdown_callback = on_zero
        self._countdown_default_label = self.btn_capture.text()
        self._set_countdown_active(True, f"{label_prefix} {self._countdown_remaining}…")
        # fire first tick immediately (so the first visible second counts down
        # from N-1 to 0 at the expected wall-clock pace)
        self._countdown_timer.start()

    def _countdown_tick(self) -> None:
        self._countdown_remaining -= 1
        if self._countdown_remaining <= 0:
            self._countdown_timer.stop()
            cb = self._countdown_callback
            self._countdown_callback = None
            self._set_countdown_active(False)
            if cb is not None:
                try:
                    cb()
                except Exception as e:
                    log.exception("countdown callback raised")
                    QMessageBox.critical(self, "Capture failed", str(e))
            return
        self.btn_capture.setText(f"Capturing in {self._countdown_remaining}…")

    def _set_countdown_active(self, active: bool, label_override: Optional[str] = None) -> None:
        """Disable other entry points while a countdown is running."""
        if active:
            self.btn_capture.setText(label_override or self.btn_capture.text())
        else:
            self.btn_capture.setText(self._countdown_default_label)
        for w in (self.btn_capture, self.btn_capture_window, self.btn_load_file,
                  self.btn_reset_image):
            w.setEnabled(not active)
        if not active:
            # restore normal disable/enable rules
            self._refresh_image_buttons()

    # ----- helpers to keep our own windows out of the screenshot ----- #

    def _hide_app_windows_for_capture(self) -> list:
        """
        Make every visible top-level Qt widget this app owns transparent so
        its pixels don't contribute to the screen capture. We intentionally
        use windowOpacity=0 instead of hide() because hide() plays badly
        with a modal QDialog that's still in exec() — the dialog can fail
        to come back to the foreground reliably on Windows.

        With opacity=0 the widgets remain "shown" from Qt's perspective; the
        modal event loop is unaffected and Windows' foreground-lock rules
        don't get a chance to swallow a raise() call. mss reads the composed
        screen via DWM, so a fully-transparent window contributes zero
        pixels to the capture.

        Returns a list of (widget, original_opacity) entries so restore can
        put them back exactly.
        """
        from PySide6.QtWidgets import QApplication
        dimmed: list = []
        for w in QApplication.topLevelWidgets():
            if not w.isVisible():
                continue
            try:
                dimmed.append((w, w.windowOpacity()))
                w.setWindowOpacity(0.0)
            except Exception:
                log.debug("failed to dim widget for capture", exc_info=True)
        # Let Qt paint the transparency before mss grabs pixels.
        try:
            QApplication.processEvents()
        except Exception:
            pass
        return dimmed

    def _restore_app_windows(self, dimmed_state: list) -> None:
        """Restore the original opacity of every widget we dimmed."""
        for w, original_opacity in dimmed_state:
            try:
                w.setWindowOpacity(original_opacity)
            except Exception:
                log.debug("failed to restore widget opacity", exc_info=True)
        # Ensure the calibration dialog ends up on top of Tradovate again,
        # since we just activated Tradovate for the capture.
        cal_dialog = self.window()
        if cal_dialog is not None and cal_dialog.isVisible():
            try:
                cal_dialog.raise_()
                cal_dialog.activateWindow()
            except Exception:
                log.debug("failed to raise calibration dialog", exc_info=True)
        # Belt-and-suspenders: retry the raise once after a short delay in
        # case Windows suppressed the first one due to foreground-lock.
        def _raise_again():
            try:
                if cal_dialog is not None and cal_dialog.isVisible():
                    cal_dialog.raise_()
                    cal_dialog.activateWindow()
            except Exception:
                log.debug("second-raise failed", exc_info=True)
        QTimer.singleShot(150, _raise_again)

    def _capture_raw_monitor(self, idx: int):
        """Just the mss capture. No hide/show, no UI updates."""
        with mss.mss() as sct:
            mon = sct.monitors[idx]
            raw = np.array(sct.grab(mon))
            bgr = iu.bgra_to_bgr(raw)
        return bgr, dict(mon)  # copy monitor dict in case sct closes

    def _grab_full_monitor(self) -> None:
        """Plain 'Capture screen' final step. Briefly hides our windows so they
        don't appear in the screenshot, captures, then restores."""
        idx = self.monitor_combo.currentData()
        if idx is None:
            QMessageBox.warning(self, "No monitor", "No monitor selected")
            return

        hidden = self._hide_app_windows_for_capture()
        # schedule the actual capture on a QTimer so Qt can finish hiding
        # + the window compositor catches up before mss grabs pixels.
        QTimer.singleShot(150, lambda: self._finish_grab(idx, hidden, source="capture",
                                                         log_tag="monitor"))

    def _capture_specific_window(self) -> None:
        """Pick a window by title. Hide our windows, activate the target, wait
        for it to repaint, capture the full monitor, then restore our windows.
        No visible countdown — the operator already chose what to capture."""
        picker = WindowPickerDialog(self)
        if not picker.exec():
            return
        choice = picker.selected_choice
        if choice is None:
            return

        # Hide our windows BEFORE activating the target — otherwise our
        # always-on-top dialog keeps intercepting focus and covering pixels.
        hidden = self._hide_app_windows_for_capture()

        err = WindowPickerDialog.activate(choice)
        if err is not None:
            self._restore_app_windows(hidden)
            QMessageBox.warning(
                self, "Activate failed",
                f"Could not activate {choice.title!r}:\n{err}\n\n"
                "Bring the target window to the front manually and retry."
            )
            return

        idx = self.monitor_combo.currentData() or 1
        # 500ms for focus switch + repaint; longer than the plain-capture path
        # since the target may have been minimized or behind other windows.
        QTimer.singleShot(500, lambda: self._finish_grab(
            idx, hidden, source="capture",
            log_tag=f"picker '{choice.title[:40]}'",
        ))

    def _finish_grab(self, idx: int, hidden: list, source: str, log_tag: str) -> None:
        """Common tail of the two capture flows: grab pixels, restore windows,
        update the canvas. Restore runs in a `finally` so we never leave the
        operator staring at a blank screen."""
        try:
            bgr, mon = self._capture_raw_monitor(idx)
        except Exception as e:
            self._restore_app_windows(hidden)
            QMessageBox.critical(self, "Capture failed", str(e))
            return
        # restore first so the canvas update happens with our windows visible
        self._restore_app_windows(hidden)
        size = (int(mon["width"]), int(mon["height"]))
        self._set_image(bgr, source=source, monitor_index=idx, size=size)
        emit_event(self.signals, "info", "calibration",
                   f"captured {log_tag}: {size[0]}x{size[1]}")

    def _load_from_file(self) -> None:
        start_dir = str(paths.screenshots_dir())
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Load screenshot",
            start_dir,
            "Images (*.png *.jpg *.jpeg *.bmp)",
        )
        if not filename:
            return
        try:
            img = cv2.imread(filename, cv2.IMREAD_COLOR)
            if img is None:
                raise ValueError("unreadable image (OpenCV returned None)")
        except Exception as e:
            QMessageBox.critical(self, "Load failed", f"Could not read {filename}:\n{e}")
            return

        h, w = img.shape[:2]

        # warn if it doesn't match the selected monitor
        target_idx = self.monitor_combo.currentData() or 1
        target_size = self._monitor_resolution(target_idx)
        if target_size is not None and target_size != (w, h):
            reply = QMessageBox.question(
                self,
                "Resolution mismatch",
                f"The image is {w}x{h}, but monitor {target_idx} is "
                f"{target_size[0]}x{target_size[1]}.\n\n"
                "Clicks will only align if the bot runs on a monitor with the same size "
                "as this screenshot. Continue anyway?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        self._set_image(img, source=f"file:{filename}",
                        monitor_index=target_idx, size=(w, h))
        emit_event(self.signals, "info", "calibration",
                   f"loaded screenshot {Path(filename).name} ({w}x{h})")

    def _monitor_resolution(self, idx: int) -> Optional[tuple[int, int]]:
        try:
            with mss.mss() as sct:
                mon = sct.monitors[idx]
                return (int(mon["width"]), int(mon["height"]))
        except Exception:
            return None

    def _set_image(self, bgr: np.ndarray, source: str,
                   monitor_index: int, size: tuple[int, int]) -> None:
        self._full_image = bgr
        self._image_source = source
        self._monitor_index = monitor_index
        self._monitor_size = size
        self.canvas.set_image(bgr)
        self._redraw_overlays()
        self._refresh_status()
        self._refresh_image_buttons()

    def _reset_image(self) -> None:
        if self._full_image is None:
            return
        if QMessageBox.question(
                self, "Change screenshot?",
                "This clears the current screenshot and ALL marked items.\n\n"
                "Continue?",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self._full_image = None
        self._image_source = "none"
        self._monitor_size = (0, 0)
        self.targets = CalibTargets()
        self.canvas.clear_image()
        self._redraw_overlays()
        self._refresh_items_list()
        self._refresh_status()
        self._refresh_image_buttons()

    def _refresh_image_buttons(self) -> None:
        loaded = self._full_image is not None
        self.btn_capture.setEnabled(not loaded)
        self.btn_capture_window.setEnabled(not loaded)
        self.btn_load_file.setEnabled(not loaded)
        self.btn_reset_image.setEnabled(loaded)

    # ---- marking ---- #

    def _start_mark(self) -> None:
        if self._full_image is None:
            QMessageBox.warning(self, "No screenshot",
                                "Capture this machine's screen or load one from file first.")
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
        key = self._selected_key()
        if key is None:
            QMessageBox.information(
                self, "Clear item",
                "Select an item in the 'Marked items' list on the right, "
                "or pick one in the 'Mark' dropdown, then try again."
            )
            return
        label = next(it[2] for it in ITEMS if it[0] == key)
        if getattr(self.targets, key) is None:
            QMessageBox.information(self, "Clear item",
                                    f"'{label}' is already empty.")
            return
        setattr(self.targets, key, None)
        self._redraw_overlays()
        self._refresh_items_list()
        emit_event(self.signals, "info", "calibration", f"cleared {label}")

    def _selected_key(self) -> Optional[str]:
        """Prefer the items_list selection; fall back to the combo."""
        row = self.items_list.currentRow()
        if row >= 0:
            item = self.items_list.currentItem()
            if item is not None:
                key = item.data(Qt.UserRole)
                if key:
                    return key
        return self.item_combo.currentData()

    def _on_items_double_clicked(self, item: QListWidgetItem) -> None:
        """Double-click a row to jump the 'Mark' dropdown to it and re-mark."""
        key = item.data(Qt.UserRole)
        if not key:
            return
        idx = self.item_combo.findData(key)
        if idx >= 0:
            self.item_combo.setCurrentIndex(idx)
            self._start_mark()

    @Slot(int, int, int, int)
    def _on_region_marked(self, left: int, top: int, width: int, height: int) -> None:
        if self._current_item_key is None:
            return
        setattr(self.targets, self._current_item_key,
                Region(left=left, top=top, width=width, height=height))
        self._auto_select_after_mark(self._current_item_key)
        self._current_item_key = None
        self._redraw_overlays()
        self._refresh_items_list()

    @Slot(int, int)
    def _on_point_marked(self, x: int, y: int) -> None:
        if self._current_item_key is None:
            return
        setattr(self.targets, self._current_item_key, Point(x=x, y=y))
        self._auto_select_after_mark(self._current_item_key)
        self._current_item_key = None
        self._redraw_overlays()
        self._refresh_items_list()

    def _auto_select_after_mark(self, key: str) -> None:
        """After marking, select the next unmarked item in the Mark combo."""
        keys = [it[0] for it in ITEMS]
        try:
            start = keys.index(key) + 1
        except ValueError:
            return
        for i in range(start, len(keys)):
            if getattr(self.targets, keys[i]) is None:
                idx = self.item_combo.findData(keys[i])
                if idx >= 0:
                    self.item_combo.setCurrentIndex(idx)
                return

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
        prev = self.items_list.currentRow()
        self.items_list.clear()
        for key, kind, label, _color, required in ITEMS:
            val = getattr(self.targets, key)
            if val is None:
                txt = f"[ ] {label}" + ("" if required else "  (optional)")
            elif kind == "region":
                txt = f"[X] {label}  {val.width}x{val.height} @ ({val.left},{val.top})"
            else:
                txt = f"[X] {label}  ({val.x}, {val.y})"
            item = QListWidgetItem(txt)
            item.setData(Qt.UserRole, key)
            self.items_list.addItem(item)
        # restore selection where possible
        if 0 <= prev < self.items_list.count():
            self.items_list.setCurrentRow(prev)

    def _refresh_status(self) -> None:
        self.lv_source.set_value(self._image_source_display(),
                                 status="ok" if self._full_image is not None else "inactive")
        self.lv_monitor.set_value(str(self._monitor_index))
        self.lv_size.set_value(f"{self._monitor_size[0]}x{self._monitor_size[1]}"
                               if self._monitor_size[0] else "—")

    def _image_source_display(self) -> str:
        if self._image_source == "none":
            return "—"
        if self._image_source == "capture":
            return "this machine's monitor"
        if self._image_source.startswith("file:"):
            return "file: " + Path(self._image_source[5:]).name
        return self._image_source

    # ---- save / load / validate ---- #

    def _missing_required(self) -> list[str]:
        missing: list[str] = []
        for key, _kind, label, _color, required in ITEMS:
            if required and getattr(self.targets, key) is None:
                missing.append(label)
        return missing

    def _save(self) -> None:
        if self._full_image is None:
            QMessageBox.warning(self, "No screenshot",
                                "Load a screenshot first (capture or file).")
            return
        missing = self._missing_required()
        if missing:
            QMessageBox.warning(self, "Missing items",
                                "Still need: " + ", ".join(missing))
            return

        a = self.targets.anchor
        anchor_crop = iu.crop(self._full_image, a.left, a.top, a.width, a.height)
        iu.save_png(anchor_crop, paths.anchor_reference_path())
        iu.save_png(self._full_image, paths.calibration_full_path())

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
            position_size_region=self.targets.position_size,
            entry_price_region=self.targets.entry_price,
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
            position_size=sm.position_size_region,
            entry_price=sm.entry_price_region,
        )

        full_path = paths.calibration_full_path()
        if full_path.exists():
            self._full_image = iu.load_png(full_path)
            self._image_source = f"file:{full_path}"
            self.canvas.set_image(self._full_image)
        else:
            self._full_image = None
            self._image_source = "none"
            self.canvas.clear_image()

        # sync monitor combo with loaded monitor_index
        idx_combo = self.monitor_combo.findData(sm.monitor_index)
        if idx_combo >= 0:
            self.monitor_combo.setCurrentIndex(idx_combo)

        self._redraw_overlays()
        self._refresh_items_list()
        self._refresh_status()
        self._refresh_image_buttons()
        QMessageBox.information(self, "Loaded",
                                f"Loaded from {paths.screen_map_path()}")

    def _validate(self, offline: bool) -> None:
        report = validate_calibration(offline=offline)
        text = "\n".join(report.lines)
        if report.ready:
            QMessageBox.information(self, "Validation passed", text)
        else:
            QMessageBox.critical(self, "Validation failed", text)

    def _reset_all_marks(self) -> None:
        if QMessageBox.question(
                self, "Reset all marks?",
                "Clear all marked items in this editor?\n"
                "(The screenshot stays loaded; the saved JSON file is not deleted.)",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self.targets = CalibTargets()
        self._redraw_overlays()
        self._refresh_items_list()

    def _delete_saved_calibration(self) -> None:
        sm_path = paths.screen_map_path()
        anchor_path = paths.anchor_reference_path()
        full_path = paths.calibration_full_path()
        overlay_path = paths.calibration_overlay_path()
        price_ref_path = paths.screenshots_dir() / "price_region_reference.png"
        candidates = [sm_path, anchor_path, full_path, overlay_path, price_ref_path]
        existing = [p for p in candidates if p.exists()]

        if not existing:
            QMessageBox.information(
                self, "Nothing to delete",
                f"No saved calibration files under\n{paths.config_dir()}\nand\n"
                f"{paths.screenshots_dir()}."
            )
            return

        file_list = "\n".join(f"  • {p}" for p in existing)
        confirm = QMessageBox.question(
            self, "Delete saved calibration?",
            "This will DELETE the saved calibration files:\n\n"
            f"{file_list}\n\n"
            "The bot will refuse to start until you calibrate again. "
            "Continue?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        deleted: list[Path] = []
        errors: list[str] = []
        for p in existing:
            try:
                p.unlink()
                deleted.append(p)
            except Exception as e:
                errors.append(f"{p.name}: {e}")

        # also clear in-memory editor state so the UI doesn't pretend the
        # calibration still exists
        self.targets = CalibTargets()
        self._full_image = None
        self._image_source = "none"
        self._monitor_size = (0, 0)
        self.canvas.clear_image()
        self._redraw_overlays()
        self._refresh_items_list()
        self._refresh_status()
        self._refresh_image_buttons()

        emit_event(self.signals, "warn", "calibration",
                   f"deleted saved calibration ({len(deleted)} files)")
        self.signals.calibration_reloaded.emit()

        msg = f"Deleted {len(deleted)} file(s):\n" + \
              "\n".join(f"  • {p}" for p in deleted)
        if errors:
            msg += "\n\nCould not delete:\n" + "\n".join(f"  • {e}" for e in errors)
            QMessageBox.warning(self, "Partially deleted", msg)
        else:
            QMessageBox.information(self, "Deleted", msg)
