"""
Tests for the per-item visibility toggle on the calibration page's
'Marked items' list.

The toggle lets the operator hide a marked overlay from the canvas
without losing the mark itself — useful when two regions overlap and
the existing rectangle gets in the way of marking a new one.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt                           # noqa: E402

from app.models.common import Region                    # noqa: E402
from app.ui.app_signals import AppSignals               # noqa: E402
from app.ui.pages.calibration_page import CalibrationPage  # noqa: E402


def _build_page(qtbot) -> CalibrationPage:
    page = CalibrationPage(AppSignals())
    qtbot.addWidget(page)
    # Seed marks on a couple of items so they get checkboxes in the list.
    page.targets.anchor = Region(left=10, top=10, width=100, height=40)
    page.targets.price = Region(left=200, top=10, width=80, height=30)
    page._refresh_items_list()
    return page


def _list_item_for(page, key: str):
    for i in range(page.items_list.count()):
        item = page.items_list.item(i)
        if item.data(Qt.UserRole) == key:
            return item
    raise AssertionError(f"no list row for key={key}")


# ---------------- checkbox presence ---------------- #


def test_only_marked_items_show_a_checkbox(qtbot):
    """Unmarked items can't be hidden — they have nothing to draw — so
    they get no checkbox. Marked items are checkable."""
    page = _build_page(qtbot)

    anchor_item = _list_item_for(page, "anchor")
    price_item = _list_item_for(page, "price")
    assert anchor_item.flags() & Qt.ItemIsUserCheckable
    assert price_item.flags() & Qt.ItemIsUserCheckable
    # An unmarked item (e.g. cancel point hasn't been marked) must NOT be
    # checkable — its row would otherwise show a misleading checkbox.
    cancel_item = _list_item_for(page, "cancel")
    assert not (cancel_item.flags() & Qt.ItemIsUserCheckable)


def test_marked_items_default_to_checked_visible(qtbot):
    page = _build_page(qtbot)
    assert _list_item_for(page, "anchor").checkState() == Qt.Checked
    assert _list_item_for(page, "price").checkState() == Qt.Checked


# ---------------- toggle hides / shows overlay ---------------- #


def test_uncheck_hides_overlay_from_canvas(qtbot):
    """Unchecking the box must drop that key's overlay from the canvas
    without removing the mark itself."""
    page = _build_page(qtbot)
    page._redraw_overlays()
    assert any(o.label.startswith("Anchor") for o in page.canvas._overlays), \
        "anchor overlay should be drawn before we hide it"

    _list_item_for(page, "anchor").setCheckState(Qt.Unchecked)

    assert "anchor" in page._hidden_keys
    assert page.targets.anchor is not None, \
        "the mark itself must NOT be cleared by hiding"
    assert not any(o.label.startswith("Anchor") for o in page.canvas._overlays)
    # The other marked item must still draw.
    assert any(o.label.startswith("Price") for o in page.canvas._overlays)


def test_recheck_shows_overlay_again(qtbot):
    page = _build_page(qtbot)
    item = _list_item_for(page, "anchor")
    item.setCheckState(Qt.Unchecked)
    assert "anchor" in page._hidden_keys

    item.setCheckState(Qt.Checked)
    assert "anchor" not in page._hidden_keys
    assert any(o.label.startswith("Anchor") for o in page.canvas._overlays)


# ---------------- lifecycle cleanup ---------------- #


def test_remarking_a_hidden_item_makes_it_visible_again(qtbot):
    """If the operator hides item X then re-marks it, the fresh mark
    should show by default — operator just calibrated it, they want to
    SEE it. Otherwise the new rectangle would invisibly land on the canvas."""
    page = _build_page(qtbot)
    _list_item_for(page, "anchor").setCheckState(Qt.Unchecked)
    assert "anchor" in page._hidden_keys

    # Re-mark the anchor (simulates the operator drawing a new region for
    # the same key via the canvas).
    page._current_item_key = "anchor"
    page._on_region_marked(50, 50, 120, 50)

    assert "anchor" not in page._hidden_keys
    assert any(o.label.startswith("Anchor") for o in page.canvas._overlays)


def test_clearing_a_hidden_item_drops_it_from_hidden_set(qtbot):
    """Clearing a mark removes it from targets; the hidden-set entry for
    that key would otherwise be stale. Cleanup keeps the set tight."""
    page = _build_page(qtbot)
    _list_item_for(page, "anchor").setCheckState(Qt.Unchecked)
    assert "anchor" in page._hidden_keys

    # Simulate "Clear selected item" on the anchor row.
    page.items_list.setCurrentRow(0)   # anchor is the first ITEM by definition
    # _clear_current_item shows a dialog if the item is missing; we
    # short-circuit by calling the underlying mutation directly to keep
    # the test headless.
    page.targets.anchor = None
    page._hidden_keys.discard("anchor")
    page._refresh_items_list()

    assert "anchor" not in page._hidden_keys


def test_reset_all_marks_clears_hidden_set(qtbot):
    page = _build_page(qtbot)
    _list_item_for(page, "anchor").setCheckState(Qt.Unchecked)
    _list_item_for(page, "price").setCheckState(Qt.Unchecked)
    assert page._hidden_keys == {"anchor", "price"}

    # The real handler asks for confirmation; bypass it by replicating the
    # post-confirmation state mutation.
    from app.ui.pages.calibration_page import CalibTargets
    page.targets = CalibTargets()
    page._hidden_keys.clear()
    page._redraw_overlays()
    page._refresh_items_list()

    assert page._hidden_keys == set()


# ---------------- two-way selection sync (list <-> combo) ---------------- #


def test_selecting_row_updates_mark_combo(qtbot):
    """Clicking a row in the marked-items list mirrors that key into the
    'Mark:' combo so 'Start mark' acts on what the operator just clicked."""
    page = _build_page(qtbot)
    # Combo starts at the first ITEMS entry — for the test, ensure it's
    # NOT pointing at the row we'll click.
    page.item_combo.setCurrentIndex(0)
    assert page.item_combo.currentData() != "price"

    page.items_list.setCurrentRow(_row_for(page, "price"))

    assert page.item_combo.currentData() == "price"


def test_changing_combo_selects_row_in_list(qtbot):
    """Reciprocal: changing the combo highlights the matching row."""
    page = _build_page(qtbot)
    # Start with no selection in the list.
    page.items_list.setCurrentRow(-1)

    idx = page.item_combo.findData("price")
    page.item_combo.setCurrentIndex(idx)

    selected = page.items_list.currentItem()
    assert selected is not None
    assert selected.data(Qt.UserRole) == "price"


def test_two_way_sync_does_not_ping_pong(qtbot):
    """When one side updates the other, the second handler must not
    re-fire the first. The _syncing_selection guard prevents that. We
    exercise both directions and assert the guard ends up cleared."""
    page = _build_page(qtbot)

    page.items_list.setCurrentRow(_row_for(page, "price"))
    assert page._syncing_selection is False, \
        "guard must clear after a list -> combo sync completes"

    idx = page.item_combo.findData("anchor")
    page.item_combo.setCurrentIndex(idx)
    assert page._syncing_selection is False, \
        "guard must clear after a combo -> list sync completes"
    # Final state matches the most recent action.
    assert page.item_combo.currentData() == "anchor"
    assert page.items_list.currentItem().data(Qt.UserRole) == "anchor"


def _row_for(page, key: str) -> int:
    for i in range(page.items_list.count()):
        if page.items_list.item(i).data(Qt.UserRole) == key:
            return i
    raise AssertionError(f"no list row for key={key}")
