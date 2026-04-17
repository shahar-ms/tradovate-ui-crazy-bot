"""
Logs and events page: tabbed view of events, price parse attempts,
executions, halts, and recent screenshot thumbnails.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPushButton, QSplitter, QTabWidget,
                               QVBoxLayout, QWidget)

from app.ui.app_signals import AppSignals
from app.ui.widgets.event_table import EventTable
from app.ui.widgets.image_preview import ImagePreview
from app.ui.widgets.panel import Panel
from app.utils import paths


class LogsPage(QWidget):
    MAX_EVENTS = 2000

    def __init__(self, signals: AppSignals, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.signals = signals

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(8)

        # top filter row
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(QLabel("Level filter:"))
        self.level_filter = QComboBox()
        self.level_filter.addItems(["all", "info", "warn", "error", "debug"])
        top.addWidget(self.level_filter)

        top.addWidget(QLabel("Source filter:"))
        self.source_filter = QComboBox()
        self.source_filter.addItems(["all"])
        self.source_filter.setEditable(False)
        top.addWidget(self.source_filter)

        self.btn_clear = QPushButton("Clear")
        self.btn_open_logs = QPushButton("Open logs folder")
        self.btn_open_screens = QPushButton("Open screenshots folder")
        top.addStretch(1)
        top.addWidget(self.btn_clear)
        top.addWidget(self.btn_open_logs)
        top.addWidget(self.btn_open_screens)
        root.addLayout(top)

        # tabs
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        # events tab
        self.events = EventTable(max_rows=self.MAX_EVENTS, compact=False)
        self.tabs.addTab(self.events, "Events")

        # price-parse tab
        self.price_events = EventTable(max_rows=self.MAX_EVENTS, compact=False)
        self.tabs.addTab(self.price_events, "Price parse")

        # execution tab
        self.exec_events = EventTable(max_rows=self.MAX_EVENTS, compact=False)
        self.tabs.addTab(self.exec_events, "Execution")

        # halts tab
        self.halt_events = EventTable(max_rows=self.MAX_EVENTS, compact=False)
        self.tabs.addTab(self.halt_events, "Halts")

        # screenshots tab
        screens_tab = QWidget()
        screens_lay = QHBoxLayout(screens_tab)
        screens_lay.setContentsMargins(0, 0, 0, 0)
        self.screens_list = QListWidget()
        self.screens_list.setFixedWidth(260)
        self.screens_preview = ImagePreview("Select a screenshot to preview")
        screens_lay.addWidget(self.screens_list)
        screens_lay.addWidget(self.screens_preview, 1)
        self.tabs.addTab(screens_tab, "Screenshots")

        # wiring
        self.signals.event_logged.connect(self._on_event)
        self.signals.price_updated.connect(self._on_price)
        self.signals.execution_ack.connect(self._on_exec_ack)
        self.signals.halt_triggered.connect(self._on_halt)
        self.signals.signal_emitted.connect(self._on_signal)

        self.btn_clear.clicked.connect(self._clear_current_tab)
        self.btn_open_logs.clicked.connect(lambda: self._open_folder(paths.logs_dir()))
        self.btn_open_screens.clicked.connect(lambda: self._open_folder(paths.screenshots_dir()))
        self.screens_list.currentItemChanged.connect(self._on_screen_selected)

        # refresh screenshots list periodically
        self._refresh_screens_timer = QTimer(self)
        self._refresh_screens_timer.setInterval(3000)
        self._refresh_screens_timer.timeout.connect(self._refresh_screens)
        self._refresh_screens_timer.start()
        self._refresh_screens()

    # ---- event stream ---- #

    @Slot(dict)
    def _on_event(self, event: dict) -> None:
        level = event.get("level", "info")
        source = event.get("source", "-")
        # source filter dropdown self-populates
        if self.source_filter.findText(source) < 0:
            self.source_filter.addItem(source)
        if self._filters_match(level, source):
            self.events.append_event(event)

    def _filters_match(self, level: str, source: str) -> bool:
        lf = self.level_filter.currentText()
        sf = self.source_filter.currentText()
        if lf != "all" and lf != level:
            return False
        if sf != "all" and sf != source:
            return False
        return True

    @Slot(dict)
    def _on_price(self, tick: dict) -> None:
        accepted = tick.get("accepted")
        msg_parts = []
        if accepted:
            msg_parts.append(f"price={tick.get('price')}")
            msg_parts.append(f"conf={tick.get('confidence', 0):.1f}")
        else:
            msg_parts.append(f"REJECT {tick.get('reject_reason')}")
        self.price_events.append_event({
            "ts_ms": tick.get("ts_ms", 0),
            "level": "info" if accepted else "warn",
            "source": "price",
            "message": " ".join(msg_parts),
        })

    @Slot(dict)
    def _on_exec_ack(self, ack: dict) -> None:
        self.exec_events.append_event({
            "ts_ms": ack.get("ts_ms", 0),
            "level": "info" if ack.get("status") == "ok"
                     else "warn" if ack.get("status") == "unknown"
                     else "error",
            "source": "execution",
            "message": f"{ack.get('action')} -> {ack.get('status')} ({ack.get('message','')})",
        })

    @Slot(dict)
    def _on_signal(self, intent: dict) -> None:
        # also surface signals in execution tab for context
        self.exec_events.append_event({
            "ts_ms": intent.get("ts_ms", 0),
            "level": "info",
            "source": "strategy",
            "message": f"signal {intent.get('action')} ({intent.get('reason','')})",
        })

    @Slot(str)
    def _on_halt(self, reason: str) -> None:
        from app.utils.time_utils import now_ms
        self.halt_events.append_event({
            "ts_ms": now_ms(),
            "level": "error",
            "source": "supervisor",
            "message": reason,
        })

    # ---- screenshots ---- #

    def _refresh_screens(self) -> None:
        d = paths.screenshots_dir()
        selected = (self.screens_list.currentItem().data(Qt.UserRole)
                    if self.screens_list.currentItem() else None)
        self.screens_list.clear()
        try:
            files = sorted([p for p in d.rglob("*.png")],
                           key=lambda p: p.stat().st_mtime, reverse=True)[:500]
        except Exception:
            return
        for f in files:
            item = QListWidgetItem(str(f.relative_to(d)))
            item.setData(Qt.UserRole, str(f))
            self.screens_list.addItem(item)
            if selected and str(f) == selected:
                self.screens_list.setCurrentItem(item)

    def _on_screen_selected(self, current: Optional[QListWidgetItem], _prev):
        if current is None:
            self.screens_preview.clear_image()
            return
        path = current.data(Qt.UserRole)
        if path:
            self.screens_preview.load_path(path)

    # ---- helpers ---- #

    def _clear_current_tab(self) -> None:
        tab = self.tabs.currentWidget()
        if isinstance(tab, EventTable):
            tab.clear_events()

    @staticmethod
    def _open_folder(path: Path) -> None:
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            pass
