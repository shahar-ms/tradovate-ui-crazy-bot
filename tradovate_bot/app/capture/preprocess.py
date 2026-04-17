"""
Preprocessing recipes for price-region OCR.

Each recipe takes a BGR or BGRA numpy image and returns a single-channel
grayscale or binary image ready for Tesseract.
"""

from __future__ import annotations

from typing import Callable

import cv2
import numpy as np


Recipe = Callable[[np.ndarray], np.ndarray]


def _gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _resize(img: np.ndarray, factor: float) -> np.ndarray:
    if factor == 1.0:
        return img
    h, w = img.shape[:2]
    interp = cv2.INTER_CUBIC if factor > 1.0 else cv2.INTER_AREA
    return cv2.resize(img, (int(w * factor), int(h * factor)), interpolation=interp)


def _auto_invert_dark_on_light(gray: np.ndarray) -> np.ndarray:
    """
    Tesseract prefers dark text on a light background. If the image looks like
    light text on a dark background, invert it.
    """
    mean = float(gray.mean())
    if mean < 110:
        return cv2.bitwise_not(gray)
    return gray


def gray_only(img: np.ndarray) -> np.ndarray:
    g = _gray(img)
    return _auto_invert_dark_on_light(g)


def binary_threshold(img: np.ndarray) -> np.ndarray:
    g = gray_only(img)
    _, out = cv2.threshold(g, 127, 255, cv2.THRESH_BINARY)
    return out


def adaptive_threshold(img: np.ndarray) -> np.ndarray:
    g = gray_only(img)
    return cv2.adaptiveThreshold(
        g, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 15, 9
    )


def otsu_threshold(img: np.ndarray) -> np.ndarray:
    g = gray_only(img)
    _, out = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return out


def scaled_2x_otsu(img: np.ndarray) -> np.ndarray:
    big = _resize(img, 2.0)
    return otsu_threshold(big)


def scaled_3x_binary_close(img: np.ndarray) -> np.ndarray:
    big = _resize(img, 3.0)
    g = gray_only(big)
    _, binarized = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    return cv2.morphologyEx(binarized, cv2.MORPH_CLOSE, kernel)


RECIPES: dict[str, Recipe] = {
    "gray_only": gray_only,
    "binary_threshold": binary_threshold,
    "adaptive_threshold": adaptive_threshold,
    "otsu_threshold": otsu_threshold,
    "scaled_2x_otsu": scaled_2x_otsu,
    "scaled_3x_binary_close": scaled_3x_binary_close,
}


def make_variants(img: np.ndarray, recipes: list[str]) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    for name in recipes:
        fn = RECIPES.get(name)
        if fn is None:
            continue
        try:
            out[name] = fn(img)
        except Exception:
            continue
    return out
