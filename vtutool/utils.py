"""Shared helpers: folders, logging, saved settings and Tesseract discovery."""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_NAME = "VTU Marks Scraper Tool"
APP_VERSION = "1.0"
APP_TITLE = f"{APP_NAME} V{APP_VERSION}"


def bundle_dir() -> Path:
    """Folder that holds the program (next to the .exe when packaged)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def user_dir() -> Path:
    """Per-user folder for settings, logs and remembered credits."""
    p = Path.home() / APP_NAME
    if not p.exists():
        p.mkdir(parents=True, exist_ok=True)
        old = Path.home() / "VTU Marks Tool"  # carry over settings/credits from the previous release
        for name in ("settings.json", "credits.json"):
            try:
                if (old / name).is_file():
                    shutil.copyfile(old / name, p / name)
            except OSError:
                pass
    return p


def asset_path(name: str) -> Path | None:
    """Find a bundled asset (icon etc.) when running from source or from the PyInstaller build."""
    roots = [bundle_dir(), bundle_dir() / "_internal"]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.insert(0, Path(meipass))
    for r in roots:
        f = r / "assets" / name
        if f.is_file():
            return f
    return None


def log_dir() -> Path:
    p = user_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("vtu")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    handler = RotatingFileHandler(
        log_dir() / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


# --------------------------------------------------------------------------- JSON storage
def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data) -> None:
    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        logging.getLogger("vtu").exception("Could not write %s", path)


def load_settings() -> dict:
    return _read_json(user_dir() / "settings.json", {})


def save_settings(data: dict) -> None:
    _write_json(user_dir() / "settings.json", data)


def load_credit_memory() -> dict:
    """{subject_code: {"credits": float, "max_marks": float}} remembered from earlier runs."""
    return _read_json(user_dir() / "credits.json", {})


def save_credit_memory(data: dict) -> None:
    merged = load_credit_memory()
    merged.update(data)
    _write_json(user_dir() / "credits.json", merged)


# --------------------------------------------------------------------------- Tesseract
def find_tesseract() -> str | None:
    """Locate tesseract.exe whether we run from source, from a PyInstaller build, or an installed copy."""
    exe = "tesseract.exe" if os.name == "nt" else "tesseract"
    candidates: list[Path] = []

    env = os.environ.get("TESSERACT_CMD")
    if env:
        candidates.append(Path(env))

    roots = [bundle_dir(), Path.cwd()]
    meipass = getattr(sys, "_MEIPASS", None)  # NOTE: the original app used "_MEIPASS2" (a typo) - that broke the .exe
    if meipass:
        roots.insert(0, Path(meipass))
    for root in roots:
        candidates.append(root / "Tesseract-OCR" / exe)
        candidates.append(root / "_internal" / "Tesseract-OCR" / exe)

    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            candidates.append(Path(base) / "Tesseract-OCR" / exe)
            candidates.append(Path(base) / "Programs" / "Tesseract-OCR" / exe)

    for c in candidates:
        if c.is_file():
            return str(c)
    return shutil.which("tesseract")


def configure_tesseract() -> str | None:
    """Point pytesseract at the Tesseract we found. Returns its path, or None if missing."""
    path = find_tesseract()
    if not path:
        return None
    try:
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = path
    except Exception:
        return None
    tessdata = Path(path).parent / "tessdata"
    if (tessdata / "eng.traineddata").is_file():
        os.environ["TESSDATA_PREFIX"] = str(tessdata)
    return path
