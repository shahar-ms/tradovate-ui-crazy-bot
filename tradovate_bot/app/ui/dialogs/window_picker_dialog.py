"""
Pick a visible OS window by title. Used by the calibration page to activate
the Tradovate browser window before a timed screen capture — so the
screenshot shows Tradovate on top but keeps absolute monitor coordinates.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QVBoxLayout, QWidget)

log = logging.getLogger(__name__)


@dataclass
class WindowChoice:
    title: str
    # the underlying pygetwindow Window object (used to activate it)
    handle: object


def _list_windows() -> list[WindowChoice]:
    try:
        import pygetwindow as gw
    except Exception as e:
        log.warning("pygetwindow import failed: %s", e)
        return []
    out: list[WindowChoice] = []
    try:
        # Prefer the actual window objects so we can activate() later
        windows = gw.getAllWindows()
    except Exception:
        try:
            windows = [w for w in gw.getAllWindows()]
        except Exception as e:
            log.warning("pygetwindow.getAllWindows failed: %s", e)
            return []
    seen: set[str] = set()
    for w in windows:
        try:
            title = (w.title or "").strip()
        except Exception:
            continue
        if not title or title in seen:
            continue
        seen.add(title)
        out.append(WindowChoice(title=title, handle=w))
    out.sort(key=lambda c: c.title.lower())
    return out


class WindowPickerDialog(QDialog):
    """
    Modal dialog. Shows a filterable list of window titles. On accept, the
    caller can read `selected_choice` and optionally `activate()` the window.
    """

    def __init__(self, parent: Optional[QWidget] = None,
                 default_filter: str = "tradovate"):
        super().__init__(parent)
        self.setWindowTitle("Pick a window")
        self.setModal(True)
        self.setMinimumSize(520, 400)
        self.selected_choice: Optional[WindowChoice] = None

        self._all: list[WindowChoice] = _list_windows()

        root = QVBoxLayout(self)
        root.setSpacing(8)

        root.addWidget(QLabel("Pick the window the bot should watch "
                              "(typically the Tradovate browser tab)."))

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("tradovate, chrome, edge, …")
        self.filter_edit.setText(default_filter)
        filter_row.addWidget(self.filter_edit)
        root.addLayout(filter_row)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(lambda _: self.accept())
        root.addWidget(self.list_widget, 1)

        self.info_label = QLabel("")
        self.info_label.setProperty("role", "muted")
        root.addWidget(self.info_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Activate + capture")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.filter_edit.textChanged.connect(self._refresh)
        self._refresh()

    # ---- internals ---- #

    def _refresh(self) -> None:
        needle = (self.filter_edit.text() or "").strip().lower()
        self.list_widget.clear()
        shown = 0
        for choice in self._all:
            if needle and needle not in choice.title.lower():
                continue
            item = QListWidgetItem(choice.title)
            item.setData(Qt.UserRole, choice)
            self.list_widget.addItem(item)
            shown += 1
        # auto-select first
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)
        self.info_label.setText(f"{shown} / {len(self._all)} windows "
                                f"({'no filter' if not needle else f'filter: {needle!r}'})")

    def _on_accept(self) -> None:
        item = self.list_widget.currentItem()
        if item is None:
            return
        self.selected_choice = item.data(Qt.UserRole)
        self.accept()

    # ---- helper used by caller ---- #

    @staticmethod
    def activate(choice: WindowChoice) -> Optional[str]:
        """Bring the chosen window to the foreground. Returns error str or None."""
        try:
            w = choice.handle
            if hasattr(w, "isMinimized") and w.isMinimized:
                try:
                    w.restore()
                except Exception:
                    pass
            try:
                w.activate()
            except Exception:
                # pygetwindow on Windows sometimes raises on first activate;
                # a second attempt usually succeeds.
                w.activate()
            return None
        except Exception as e:
            log.warning("failed to activate window %r: %s", choice.title, e)
            return str(e)
