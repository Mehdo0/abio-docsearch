"""Chemins et configuration de l'application.

L'application est portable : si le dossier de l'exécutable est inscriptible,
les données vivent à côté de l'exécutable dans ``data/``. Sinon, elles vont
dans ``%LOCALAPPDATA%\\Abionic\\DocSearch`` (Windows) ou
``~/.local/share/abionic-docsearch`` (Linux, pour les tests).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

APP_NAME = "Abionic DocSearch"
APP_SLUG = "abionic-docsearch"
VERSION = "3.1.0"

DEFAULT_CONFIG: dict = {
    "roots": [],
    "exclude_dirs": [
        "__old",
        "$recycle.bin",
        "system volume information",
        ".git",
        "node_modules",
        ".svn",
        "__pycache__",
    ],
    "include_exts": [],
    "exclude_exts": [
        ".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".cab", ".iso",
        ".exe", ".msi", ".dll", ".sys", ".drv", ".so", ".dylib", ".bat", ".cmd",
        ".ps1", ".vbs", ".jar", ".tmp", ".temp", ".bak", ".old", ".orig", ".dmp",
        ".part", ".crdownload", ".lnk", ".url", ".db", ".sqlite", ".sqlite3",
        ".mdb", ".ldb", ".log",
    ],
    "exclude_names": ["thumbs.db", "desktop.ini", ".ds_store", "~$*", "*.tmp", "*.bak"],
    "threads": 8,
    "preview_cache_mb": 800,
}


def resource_dir() -> Path:
    """Dossier des ressources embarquées (UI, logo, polices)."""
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        return Path(frozen)
    return Path(__file__).resolve().parent


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".wtest", delete=True):
            pass
        return True
    except OSError:
        return False


def _portable_root() -> Path | None:
    """Dossier de l'exécutable, seulement s'il est inscriptible."""
    if not getattr(sys, "frozen", False):
        return None
    exe_dir = Path(sys.executable).resolve().parent
    if _writable(exe_dir / "data"):
        return exe_dir
    return None


def data_dir() -> Path:
    override = os.environ.get("DOCSEARCH_DATA")
    if override:
        path = Path(override).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    root = _portable_root()
    if root is not None:
        return root / "data"

    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        path = base / "Abionic" / "DocSearch"
    else:
        path = Path.home() / ".local" / "share" / APP_SLUG
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return data_dir() / "config.json"


def db_path() -> Path:
    return data_dir() / "index.db"


def preview_dir() -> Path:
    path = data_dir() / "previews"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    path = config_path()
    if path.exists():
        try:
            cfg.update(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return cfg


def save_config(cfg: dict) -> None:
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    merged.update(cfg)
    config_path().write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def ui_dir() -> Path:
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        return Path(frozen) / "docsearch" / "ui"
    return Path(__file__).resolve().parent / "ui"


def human_size(size: float | int | None) -> str:
    value = float(size or 0)
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if value < 1024 or unit == "To":
            if unit in ("o", "Ko"):
                return f"{value:.0f} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} To"
