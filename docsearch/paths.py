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
VERSION = "3.1.3"

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


def _legacy_dir() -> Path:
    """Emplacement historique, hors du dossier de l'exécutable."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "Abionic" / "DocSearch"
    return Path.home() / ".local" / "share" / APP_SLUG


def _db_bytes(path: Path) -> int:
    """Taille de l'index, -1 s'il est absent."""
    try:
        return (path / "index.db").stat().st_size
    except OSError:
        return -1


def _root_count(path: Path) -> int:
    """Nombre de dossiers analysés enregistrés dans config.json."""
    try:
        data = json.loads((path / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    roots = data.get("roots") if isinstance(data, dict) else None
    return len(roots) if isinstance(roots, list) else 0


def _library_dir(portable: Path, legacy: Path) -> Path:
    """Choisit le dossier qui contient réellement la bibliothèque.

    Un exécutable déplacé crée ``data/`` à côté de lui, vide, et masquerait la
    bibliothèque existante. On ne retient donc le dossier portable que s'il
    contient une bibliothèque au moins aussi fournie que l'historique.
    """
    portable_db, legacy_db = _db_bytes(portable), _db_bytes(legacy)
    if legacy_db <= 0:
        return portable
    if portable_db <= 0:
        return legacy
    portable_roots, legacy_roots = _root_count(portable), _root_count(legacy)
    if legacy_roots and not portable_roots:
        return legacy
    if portable_roots and not legacy_roots:
        return portable
    return portable if portable_db >= legacy_db else legacy


POINTER_NAME = "donnees.txt"


def pointer_file() -> Path:
    """Fichier qui mémorise le dossier de données choisi par l'utilisateur."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / POINTER_NAME
    return _legacy_dir() / POINTER_NAME


def set_data_dir_pointer(path: Path) -> Path:
    """Enregistre un dossier de données et renvoie le fichier écrit."""
    target = pointer_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(path), encoding="utf-8")
    return target


def _pointer_dir() -> Path | None:
    try:
        raw = pointer_file().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_dir() else None


def data_dir() -> Path:
    override = os.environ.get("DOCSEARCH_DATA")
    if override:
        path = Path(override).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    choisi = _pointer_dir()
    if choisi is not None:
        return choisi

    root = _portable_root()
    if root is not None:
        path = _library_dir(root / "data", _legacy_dir())
    else:
        path = _legacy_dir()
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
