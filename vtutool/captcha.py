"""CAPTCHA reading for the VTU result page (Tesseract OCR).

The VTU captcha is dark-grey (RGB 102,102,102) distorted text drawn over a background of lighter words.
Measured on 50 real captchas: the old filter let background words through and the text was never straightened,
so almost every read failed. This version
  1. keeps only the dark-grey pixels (plus their anti-aliased edge) and drops small specks,
  2. tries the text at several rotation angles and keeps the 6-character result Tesseract is most confident about.
On a set of real captchas this gives roughly 65-70 % fully correct reads (most of the rest differ only in
letter case or in look-alikes such as 8/S, 0/O), and 100 % six-character results.
"""
from __future__ import annotations

import io
import re
from collections import deque

import numpy as np
from PIL import Image

_ALNUM = re.compile(r"[^A-Za-z0-9]")
_WHITELIST = "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
_ANGLES = range(-16, 17, 4)
_RESAMPLE = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
_BICUBIC = getattr(getattr(Image, "Resampling", Image), "BICUBIC")


class TesseractMissing(RuntimeError):
    """Tesseract OCR could not be found / started."""


def _grow(mask: np.ndarray) -> np.ndarray:
    p = np.pad(mask, 1)
    out = np.zeros_like(mask)
    h, w = mask.shape
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            out |= p[dy:dy + h, dx:dx + w]
    return out


def _big_components(mask: np.ndarray, min_size: int) -> np.ndarray:
    """Keep only 8-connected blobs of at least min_size pixels (pure python, ~12k pixels: a few ms)."""
    h, w = mask.shape
    seen = np.zeros((h, w), dtype=bool)
    keep = np.zeros((h, w), dtype=bool)
    ys, xs = np.nonzero(mask)
    for y0, x0 in zip(ys.tolist(), xs.tolist()):
        if seen[y0, x0]:
            continue
        seen[y0, x0] = True
        blob = [(y0, x0)]
        q = deque(blob)
        while q:
            cy, cx = q.popleft()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        blob.append((ny, nx))
                        q.append((ny, nx))
        if len(blob) >= min_size:
            for y, x in blob:
                keep[y, x] = True
    return keep


def _text_mask(img: Image.Image) -> np.ndarray:
    """Boolean mask of the captcha text pixels."""
    a = np.asarray(img.convert("RGB")).astype(int)
    grey = (abs(a[..., 0] - a[..., 1]) <= 6) & (abs(a[..., 1] - a[..., 2]) <= 6)
    lum = a.mean(axis=2)
    core = _big_components(grey & (lum <= 118), 12)         # solid text colour, without specks
    edge = grey & (lum <= 175) & _grow(core)                # soft anti-aliased edge of the letters
    return core | edge


def _to_image(mask: np.ndarray, scale: int = 2, pad: int = 20) -> Image.Image:
    im = Image.fromarray(np.where(mask, 0, 255).astype(np.uint8))
    im = im.resize((im.width * scale, im.height * scale), _RESAMPLE)
    out = Image.new("L", (im.width + 2 * pad, im.height + 2 * pad), 255)
    out.paste(im, (pad, pad))
    return out


def _ocr_conf(img: Image.Image, config: str) -> tuple[str, float]:
    import pytesseract

    d = pytesseract.image_to_data(img, config=config, output_type=pytesseract.Output.DICT)
    text, confs = "", []
    for t, c in zip(d["text"], d["conf"]):
        t = _ALNUM.sub("", t)
        if t:
            text += t
            confs.append(float(c))
    return text, (sum(confs) / len(confs) if confs else 0.0)


def _ocr(img: Image.Image, config: str = "") -> str:
    import pytesseract

    return _ALNUM.sub("", pytesseract.image_to_string(img, config=config))


def _legacy_guess(raw: Image.Image) -> str:
    """Fallback for captchas in some other colour scheme: the original app's grey filter."""
    a = np.asarray(raw.convert("RGB"))
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    m = (r == g) & (g == b) & (r >= 100) & (r < 160)
    out = np.full(a.shape, 255, dtype=np.uint8)
    out[m] = a[m]
    return _ocr(Image.fromarray(out))


def solve_captcha(png_bytes: bytes) -> str:
    """Return the OCR guess for the captcha (6 characters when successful, possibly shorter/longer if not)."""
    import pytesseract

    raw = Image.open(io.BytesIO(png_bytes))
    try:
        mask = _text_mask(raw)
        if mask.sum() < 150:                       # nothing that looks like the usual dark-grey text
            return _legacy_guess(raw)
        base = _to_image(mask)
        results = []
        for angle in _ANGLES:
            im = base.rotate(angle, expand=True, fillcolor=255, resample=_BICUBIC)
            results.append(_ocr_conf(im, f"--psm 7 {_WHITELIST}"))
    except (pytesseract.TesseractNotFoundError, FileNotFoundError, PermissionError, OSError) as exc:
        raise TesseractMissing(
            "Tesseract OCR was not found. Put the 'Tesseract-OCR' folder next to the program "
            "(or install Tesseract) and try again."
        ) from exc
    six = [r for r in results if len(r[0]) == 6]
    pool = six or results
    return max(pool, key=lambda r: r[1])[0] if pool else ""
