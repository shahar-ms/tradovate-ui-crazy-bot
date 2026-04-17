"""
Pluggable OCR reader. v1 ships with a Tesseract backend.

Protocol:
    class OCRReader(Protocol):
        def read(self, image: np.ndarray) -> OCRResult: ...
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import Optional, Protocol

import numpy as np

from .models import OCRResult

log = logging.getLogger(__name__)


class OCRReader(Protocol):
    def read(self, image: np.ndarray) -> OCRResult: ...


class TesseractOCRReader:
    """
    Tesseract-backed reader. Whitelists digits/./- and uses PSM 7 (single line).
    """

    def __init__(
        self,
        whitelist: str = "0123456789.-",
        psm: int = 7,
        tesseract_cmd: Optional[str] = None,
    ):
        import pytesseract  # import lazily so tests without tesseract still load the module

        self.pytesseract = pytesseract
        resolved_cmd = tesseract_cmd or os.environ.get("TESSERACT_CMD")
        if resolved_cmd:
            pytesseract.pytesseract.tesseract_cmd = resolved_cmd
        elif not shutil.which("tesseract"):
            default_win = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
            if os.path.exists(default_win):
                pytesseract.pytesseract.tesseract_cmd = default_win

        self.config = f"--psm {psm} -c tessedit_char_whitelist={whitelist}"

    def read(self, image: np.ndarray) -> OCRResult:
        try:
            data = self.pytesseract.image_to_data(
                image, config=self.config,
                output_type=self.pytesseract.Output.DICT,
            )
        except Exception as e:
            log.debug("tesseract read failed: %s", e)
            return OCRResult(raw_text="", confidence=0.0, engine_name="tesseract")

        texts: list[str] = []
        confs: list[float] = []
        for txt, conf in zip(data.get("text", []), data.get("conf", [])):
            if txt is None or str(txt).strip() == "":
                continue
            try:
                c = float(conf)
            except (TypeError, ValueError):
                c = -1.0
            if c < 0:
                # tesseract uses -1 for "no confidence available"; skip but keep text
                texts.append(str(txt).strip())
                continue
            texts.append(str(txt).strip())
            confs.append(c)

        raw = " ".join(t for t in texts if t).strip()
        conf = max(confs) if confs else 0.0
        return OCRResult(raw_text=raw, confidence=conf, engine_name="tesseract")


class StubOCRReader:
    """Returns a fixed result. Useful for tests that don't have Tesseract installed."""

    def __init__(self, raw_text: str = "", confidence: float = 0.0):
        self._raw_text = raw_text
        self._confidence = confidence

    def read(self, image: np.ndarray) -> OCRResult:  # noqa: ARG002
        return OCRResult(raw_text=self._raw_text, confidence=self._confidence, engine_name="stub")


def build_reader(backend: str = "tesseract") -> OCRReader:
    if backend == "tesseract":
        return TesseractOCRReader()
    if backend == "stub":
        return StubOCRReader()
    raise ValueError(f"Unknown OCR backend: {backend}")
