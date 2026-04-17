"""
Ring-buffer event table. New rows append; oldest roll off. Used on the
dashboard (compact) and logs page (wide).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem

from app.ui.theme import BROKEN_RED, DEGRADED_YELLOW, OK_GREEN, TEXT_MUTED


LEVEL_COLORS = {
    "info": None,
    "warn": QColor(DEGRADED_YELLOW),
    "error": QColor(BROKEN_RED),
    "debug": QColor(TEXT_MUTED),
    "ok": QColor(OK_GREEN),
}


def _fmt_ts(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%H:%M:%S")


class EventTable(QTableWidget):
    def __init__(self, max_rows: int = 200, compact: bool = False, parent=None):
        super().__init__(parent)
        self.max_rows = max_rows
        cols = ["Time", "Level", "Source", "Message"]
        self.setColumnCount(len(cols))
        self.setHorizontalHeaderLabels(cols)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)

        h = self.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.Stretch)

        if compact:
            self.setFixedHeight(180)

    def append_event(self, event: dict) -> None:
        row = self.rowCount()
        self.insertRow(row)
        level = str(event.get("level", "info")).lower()
        items = [
            QTableWidgetItem(_fmt_ts(int(event.get("ts_ms", 0)))),
            QTableWidgetItem(level),
            QTableWidgetItem(str(event.get("source", "-"))),
            QTableWidgetItem(str(event.get("message", ""))),
        ]
        color: Optional[QColor] = LEVEL_COLORS.get(level)
        for i, it in enumerate(items):
            it.setFlags(it.flags() ^ Qt.ItemIsEditable)
            if color is not None:
                it.setForeground(color)
            self.setItem(row, i, it)

        # trim
        while self.rowCount() > self.max_rows:
            self.removeRow(0)

        # auto-scroll to the bottom
        self.scrollToBottom()

    def clear_events(self) -> None:
        self.setRowCount(0)
