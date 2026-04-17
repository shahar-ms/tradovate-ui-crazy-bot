from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def bgra_to_bgr(img: np.ndarray) -> np.ndarray:
    if img.ndim == 3 and img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    return img


def to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def save_png(img: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img)


def load_png(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise FileNotFoundError(f"Could not read image: {path}")
    return img


def crop(img: np.ndarray, left: int, top: int, width: int, height: int) -> np.ndarray:
    return img[top : top + height, left : left + width].copy()


def similarity_score(a: np.ndarray, b: np.ndarray) -> float:
    """
    Normalized similarity in [0, 1]. Uses mean absolute difference on grayscale,
    resized to match. 1.0 = identical, 0.0 = fully different.
    """
    ga = to_gray(a)
    gb = to_gray(b)
    if ga.shape != gb.shape:
        gb = cv2.resize(gb, (ga.shape[1], ga.shape[0]), interpolation=cv2.INTER_AREA)
    diff = np.abs(ga.astype(np.int16) - gb.astype(np.int16)).astype(np.float32)
    mad = float(diff.mean())
    return max(0.0, 1.0 - mad / 255.0)


def draw_point(img: np.ndarray, x: int, y: int, color=(0, 0, 255), label: str | None = None) -> None:
    cv2.circle(img, (x, y), 10, color, 2)
    cv2.circle(img, (x, y), 2, color, -1)
    if label:
        cv2.putText(
            img, label, (x + 12, y - 8),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA,
        )


def draw_region(img: np.ndarray, left: int, top: int, width: int, height: int,
                color=(0, 255, 0), label: str | None = None) -> None:
    cv2.rectangle(img, (left, top), (left + width, top + height), color, 2)
    if label:
        cv2.putText(
            img, label, (left, max(0, top - 6)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA,
        )
