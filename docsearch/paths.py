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
VERSION = "3.3.0"

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


def app_dir() -> Path | None:
    """Dossier de l'exécutable (``None`` quand l'app tourne depuis les sources)."""
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent


def _portable_root() -> Path | None:
    """Dossier de l'exécutable, seulement s'il est inscriptible."""
    exe_dir = app_dir()
    if exe_dir is None:
        return None
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
    """Dossier écrit dans ``donnees.txt``, qu'il contienne un index ou non."""
    try:
        raw = pointer_file().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_dir() else None


def has_index(path: Path) -> bool:
    """Vrai si ``path`` contient réellement un index (``index.db``)."""
    try:
        return (path / "index.db").is_file()
    except OSError:
        return False


# Origine du dossier retenu, affichée telle quelle dans l'interface.
SOURCE_ENV = "variable DOCSEARCH_DATA"
SOURCE_POINTER = "fichier donnees.txt"
SOURCE_APP = "dossier de l'application"
SOURCE_USER = "dossier utilisateur"


def resolve_data_dir() -> tuple[Path, str, list[str]]:
    """Dossier de données retenu, son origine et les avertissements éventuels.

    Un ``donnees.txt`` n'est pris en compte que s'il désigne un dossier qui
    contient un ``index.db``. Sinon il est ignoré : c'est ce cas qui faisait
    afficher « 0 document » alors que la bibliothèque existait ailleurs.
    """
    warnings: list[str] = []

    override = os.environ.get("DOCSEARCH_DATA")
    if override:
        path = Path(override).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path, SOURCE_ENV, warnings

    pointe = _pointer_dir()
    if pointe is not None:
        cible = pointe
        if not has_index(cible) and has_index(cible / "data"):
            # L'utilisateur a indiqué le dossier parent (souvent le dossier de
            # l'exe) : la bibliothèque est dans son sous-dossier « data ».
            cible = cible / "data"
            warnings.append(
                f"{POINTER_NAME} désignait le dossier parent ; le sous-dossier "
                "« data » est utilisé et le pointeur a été corrigé."
            )
            try:
                set_data_dir_pointer(cible)
            except OSError:
                pass
        if has_index(cible):
            return cible, SOURCE_POINTER, warnings
        warnings.append(
            f"{POINTER_NAME} désigne « {pointe} », mais ce dossier ne contient pas "
            "index.db. Il est ignoré : l'application utilise la bibliothèque réelle."
        )

    dossier_app = app_dir()
    portable = dossier_app / "data" if dossier_app is not None else None
    legacy = _legacy_dir()

    if portable is not None and has_index(portable):
        if has_index(legacy):
            choisi = _library_dir(portable, legacy)
            if choisi != portable:
                warnings.append(
                    "Deux bibliothèques existent. Celle de "
                    f"« {legacy} » contient plus de documents et est utilisée. "
                    f"Pour lui préférer « {portable} », utilisez « Choisir… »."
                )
            return choisi, SOURCE_APP if choisi == portable else SOURCE_USER, warnings
        return portable, SOURCE_APP, warnings

    if has_index(legacy):
        return legacy, SOURCE_USER, warnings

    # Première utilisation : aucun index nulle part.
    if portable is not None and _writable(portable):
        return portable, SOURCE_APP, warnings
    legacy.mkdir(parents=True, exist_ok=True)
    return legacy, SOURCE_USER, warnings


def data_dir() -> Path:
    path, _source, _warnings = resolve_data_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _dir_bytes(path: Path) -> int:
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                total += entry.stat().st_size
    except OSError:
        pass
    return total


def _file_bytes(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def data_info() -> dict:
    """Tout ce qu'il faut pour expliquer où vivent les données."""
    path, source, warnings = resolve_data_dir()
    dossier_app = app_dir()
    portable = dossier_app / "data" if dossier_app is not None else None
    legacy = _legacy_dir()

    candidates = [
        {"label": "Dossier de l'application", "path": str(portable) if portable else "",
         "has_index": has_index(portable) if portable else False,
         "bytes": max(_db_bytes(portable), 0) if portable else 0},
        {"label": "Dossier utilisateur", "path": str(legacy),
         "has_index": has_index(legacy), "bytes": max(_db_bytes(legacy), 0)},
    ]
    pointe = _pointer_dir()
    if pointe is not None:
        candidates.insert(0, {"label": POINTER_NAME, "path": str(pointe),
                              "has_index": has_index(pointe),
                              "bytes": max(_db_bytes(pointe), 0)})

    return {
        "path": str(path),
        "source": source,
        "warnings": warnings,
        "has_index": has_index(path),
        "writable": _writable(path),
        "index_bytes": max(_db_bytes(path), 0),
        "index_human": human_size(max(_db_bytes(path), 0)),
        "config_bytes": _file_bytes(path / "config.json"),
        "previews_bytes": _dir_bytes(path / "previews"),
        "scan_log": str(path / "scan-errors.log")
        if (path / "scan-errors.log").exists() else "",
        "pointer": str(pointer_file()),
        "exe_dir": str(dossier_app) if dossier_app else "",
        "candidates": candidates,
    }


def config_path() -> Path:
    return data_dir() / "config.json"


def db_path() -> Path:
    return data_dir() / "index.db"


def preview_dir() -> Path:
    path = data_dir() / "previews"
    path.mkdir(parents=True, exist_ok=True)
    return path


def as_list(value) -> list[str]:
    """Liste de chaînes, même si le fichier de réglages contient une chaîne.

    Un ancien ``config.json`` pouvait écrire ``"include_exts": ".pdf,.docx"``.
    Itérée telle quelle, cette chaîne donnait ``{.``, ``p``, ``d``…``}`` : tous
    les fichiers étaient alors ignorés et l'analyse annonçait zéro document.
    """
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.replace(";", ",").split(",")
        return [part.strip() for part in parts if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    path = config_path()
    if path.exists():
        try:
            cfg.update(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    for field in ("roots", "exclude_dirs", "include_exts", "exclude_exts", "exclude_names"):
        cfg[field] = as_list(cfg.get(field))
    try:
        cfg["threads"] = max(1, min(32, int(cfg.get("threads") or 8)))
    except (TypeError, ValueError):
        cfg["threads"] = 8
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
