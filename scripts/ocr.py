"""OCR text source for scanned question booklets and answer keys.

Pages are rendered at 200 dpi and read with RapidOCR (PP-OCRv5 English model).
Results are cached per page under .ocr_cache/ so re-running the extractor is
fast. Lines come back in PDF point coordinates.
"""
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

import numpy as np
import pymupdf

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".ocr_cache"
DPI = 200
_engine = None

# OCR renderings of an option label: (A) (a) [A] (A] [A| A) () ) ...
MARKER = re.compile(r"^[\(\[\{|]?([A-Da-d]?)[\)\]\}|]$|^[\(\[\{|]([A-Da-d])[\)\]\}|]?$")


def engine():
    global _engine
    if _engine is None:
        from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

        _engine = RapidOCR(params={
            "Rec.lang_type": LangRec.EN,
            "Rec.ocr_version": OCRVersion.PPOCRV5,
            "Rec.model_type": ModelType.MOBILE,
            "Global.log_level": "error",
        })
    return _engine


@lru_cache(maxsize=None)
def _doc_id(path):
    return hashlib.sha1(Path(path).read_bytes()).hexdigest()[:16]


def page_lines(page):
    """[(x0, y0, x1, y1, text, score)] for one page, in PDF points (cached)."""
    doc_id = _doc_id(page.parent.name)
    path = CACHE / doc_id / f"p{page.number + 1:03d}.json"
    if path.exists():
        return json.loads(path.read_text())
    z = DPI / 72
    pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z), colorspace=pymupdf.csGRAY)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    res = engine()(img)
    lines = []
    for box, text, score in zip(res.boxes if res.boxes is not None else [], res.txts or [], res.scores or []):
        xs, ys = [float(p[0]) / z for p in box], [float(p[1]) / z for p in box]
        lines.append((min(xs), min(ys), max(xs), max(ys), text, float(score)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(lines))
    return lines


def word_chars(x0, y0, x1, y1, text):
    """Spread a line's characters evenly across its box, as pseudo char tuples."""
    n = max(len(text), 1)
    w = (x1 - x0) / n
    size = (y1 - y0) * 0.8
    return [(ch, x0 + i * w, y0, x0 + (i + 1) * w, y1, "OCR", "latin", size, i) for i, ch in enumerate(text)]


def columns(lines, width):
    """Split lines into reading columns: two when the page has a clear centre gutter."""
    mid = width / 2
    narrow = [l for l in lines if l[2] - l[0] < width * 0.55]
    crossing = sum(1 for l in narrow if l[0] < mid - 6 < l[2] - 12 and l[2] > mid + 12)
    left = sum(1 for l in narrow if l[2] <= mid + 6)
    right = sum(1 for l in narrow if l[0] >= mid - 6)
    if left > 8 and right > 8 and crossing < 3:
        return [(0, mid), (mid, width)]
    return [(0, width)]
