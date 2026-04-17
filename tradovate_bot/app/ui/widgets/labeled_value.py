"""Horizontal 'label: value' pair used across dashboard cards."""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget


class LabeledValue(QWidget):
    def __init__(self, label: str, initial: str = "-", value_big: bool = False,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self._label = QLabel(label)
        self._label.setProperty("role", "muted")
        self._label.setMinimumWidth(140)
        self._label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

        self._value = QLabel(initial)
        if value_big:
            self._value.setProperty("role", "big")
        self._value.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        lay.addWidget(self._label)
        lay.addWidget(self._value, 1)

    def set_value(self, v: str, status: Optional[str] = None) -> None:
        self._value.setText(v)
        if status is not None:
            self._value.setProperty("status", status)
            self._value.style().unpolish(self._value)
            self._value.style().polish(self._value)
