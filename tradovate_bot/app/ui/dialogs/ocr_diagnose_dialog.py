"""
Live OCR diagnostics.

Shows the user four things, refreshing ~3 times a second:
  - the live price-region crop from the configured monitor (2x upscaled)
  - the current raw OCR text
  - OCR confidence
  - accepted/rejected counts + last reject reason

This is the fastest way to answer "why is step 2 not progressing?" — the
operator can visually confirm whether the price region actually contains
the price, and whether another window is covering it.
"""

from __future__ import annotations

import logging
from typing import Optional

import cv2
import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                               QVBoxLayout, QWidget)

from app.capture.ocr_reader import build_reader
from app.capture.preprocess import make_variants
from app.capture.screen_capture import ScreenCapture
from app.models.common import ScreenMap
from app.models.config import load_bot_config, load_screen_map
from app.ui.theme import BORDER, BROKEN_RED, OK_GREEN, PANEL, TEXT, TEXT_MUTED
from app.ui.ui_state import UiState
from app.utils import paths

log = logging.getLogger(__name__)


PREVIEW_SCALE = 3    # visual upscaling only; OCR still runs on the original
REFRESH_MS = 350


class OcrDiagnoseDialog(QDialog):
    def __init__(self, state: UiState, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.state = state
        self.setWindowTitle("Diagnose price OCR")
        self.setMinimumSize(640, 360)

        self._screen_map: Optional[ScreenMap] = None
        self._capture: Optional[ScreenCapture] = None
        self._min_confidence: float = 0.0
        self._try_load_config()

        root = QVBoxLayout(self)
        root.setSpacing(10)

        # helper text
        intro = QLabel(
            "A live crop of your calibrated <b>price region</b>, refreshed "
            "three times a second. The raw OCR text underneath is what the "
            "bot sees <i>right now</i>. If the image shows something other "
            "than the Tradovate price, a window is covering it or the "
            "calibration is misaligned."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        root.addWidget(intro)

        # preview
        self.preview_lbl = QLabel()
        self.preview_lbl.setAlignment(Qt.AlignCenter)
        self.preview_lbl.setMinimumHeight(120)
        self.preview_lbl.setStyleSheet(
            f"background-color: #0b0d12; border: 1px solid {BORDER}; "
            f"color: {TEXT_MUTED};"
        )
        self.preview_lbl.setText("loading…")
        root.addWidget(self.preview_lbl)

        # raw OCR text + confidence
        text_row = QHBoxLayout()
        text_row.addWidget(QLabel("raw OCR:"))
        self.raw_lbl = QLabel("—")
        self.raw_lbl.setStyleSheet(
            f"font-family: Consolas, monospace; font-size: 18px; "
            f"font-weight: 700; color: {TEXT}; "
            f"background-color: {PANEL}; padding: 4px 8px; "
            f"border: 1px solid {BORDER}; border-radius: 4px;"
        )
        text_row.addWidget(self.raw_lbl, 1)

        self.conf_lbl = QLabel("conf —")
        self.conf_lbl.setStyleSheet(f"color: {TEXT_MUTED};")
        text_row.addWidget(self.conf_lbl)
        root.addLayout(text_row)

        # counters
        self.counters_lbl = QLabel("")
        self.counters_lbl.setStyleSheet("font-size: 11px;")
        self.counters_lbl.setWordWrap(True)
        root.addWidget(self.counters_lbl)

        # status / error line
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet(f"color: {BROKEN_RED}; font-size: 11px;")
        self.status_lbl.setWordWrap(True)
        root.addWidget(self.status_lbl)

        root.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        buttons.accepted.connect(self.close)
        root.addWidget(buttons)

        # Own OCR reader, so diagnostics work even before the bot starts.
        try:
            self._reader = build_reader("tesseract")
        except Exception as e:
            self._reader = None
            self.status_lbl.setText(f"OCR reader unavailable: {e}")

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._tick()

    # ---- setup ---- #

    def _try_load_config(self) -> None:
        try:
            self._screen_map = load_screen_map(paths.screen_map_path())
            self._capture = ScreenCapture(self._screen_map.monitor_index)
            self._min_confidence = load_bot_config(paths.bot_config_path()).min_ocr_confidence
        except Exception as e:
            log.warning("diagnose: config load failed: %s", e)

    # ---- tick ---- #

    def _tick(self) -> None:
        if self._screen_map is None or self._capture is None:
            self.status_lbl.setText(
                "No calibration loaded. Complete Step 1 first, then reopen this dialog."
            )
            return

        try:
            crop = self._capture.grab_region(self._screen_map.price_region)
        except Exception as e:
            self.status_lbl.setText(f"Screen capture failed: {e}")
            return

        self._render_preview(crop)
        self._run_ocr(crop)
        self._render_counters()

    def _render_preview(self, crop: np.ndarray) -> None:
        # upscale for visibility only
        h, w = crop.shape[:2]
        big = cv2.resize(crop, (w * PREVIEW_SCALE, h * PREVIEW_SCALE),
                         interpolation=cv2.INTER_NEAREST)
        rgb = big[..., ::-1].copy()  # BGR -> RGB
        img = QImage(rgb.data, rgb.shape[1], rgb.shape[0],
                     3 * rgb.shape[1], QImage.Format_RGB888).copy()
        pix = QPixmap.fromImage(img)
        # fit into the label while keeping aspect ratio
        self.preview_lbl.setPixmap(pix.scaled(self.preview_lbl.width(),
                                              self.preview_lbl.height(),
                                              Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _run_ocr(self, crop: np.ndarray) -> None:
        if self._reader is None:
            return
        try:
            # Run each enabled preprocess variant and report the best candidate.
            best_text = ""
            best_conf = 0.0
            variants = make_variants(crop, ["gray_only", "otsu_threshold",
                                            "scaled_2x_otsu", "scaled_3x_binary_close"])
            for _name, img in variants.items():
                try:
                    r = self._reader.read(img)
                except Exception:
                    continue
                if r.raw_text and r.confidence >= best_conf:
                    best_text = r.raw_text
                    best_conf = r.confidence
            self.raw_lbl.setText(best_text or "—")
            self.conf_lbl.setText(f"conf {best_conf:.0f}")
            ok = best_conf >= self._min_confidence
            color = OK_GREEN if ok else BROKEN_RED
            self.conf_lbl.setStyleSheet(f"color: {color}; font-weight: 600;")
            self.status_lbl.setText("")
        except Exception as e:
            self.status_lbl.setText(f"OCR error: {e}")

    def _render_counters(self) -> None:
        s = self.state
        line = (f"bot counters — accepted: {s.accepted_tick_count}   "
                f"rejected: {s.rejected_tick_count}   "
                f"health: {s.price_stream_health}")
        if s.last_reject_reason:
            line += f"   last reject: {s.last_reject_reason}"
        self.counters_lbl.setText(line)

    # ---- cleanup ---- #

    def closeEvent(self, event):  # noqa: N802
        self._timer.stop()
        if self._capture is not None:
            try:
                self._capture.close()
            except Exception:
                pass
        super().closeEvent(event)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        # re-render the preview at the new size on next tick
        self._tick()
