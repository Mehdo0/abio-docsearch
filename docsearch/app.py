"""Fenêtre native et pont JavaScript <-> Python.

Aucun serveur HTTP, aucun localhost : la page est chargée depuis les fichiers
embarqués et les appels passent par l'API JavaScript de pywebview.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from . import preview
from .catalog import Catalog
from .paths import (
    APP_NAME,
    VERSION,
    data_dir,
    db_path,
    human_size,
    load_config,
    save_config,
    ui_dir,
)


class Api:
    def __init__(self) -> None:
        self.catalog = Catalog(db_path())
        self.config = load_config()
        self._window = None
        self._scan_lock = threading.Lock()
        self._scan: dict = {
            "running": False,
            "phase": "idle",
            "files": 0,
            "dirs": 0,
            "added": 0,
            "visited": 0,
            "errors": 0,
            "excluded": 0,
            "current": "",
            "elapsed": 0,
            "result": None,
            "error": None,
        }
        self._cancel = threading.Event()

    def attach(self, window) -> None:
        self._window = window

    # -- bootstrap ----------------------------------------------------------
    def bootstrap(self) -> dict:
        try:
            stats = self.catalog.stats()
        except Exception as exc:
            stats = {"files": 0, "dirs": 0, "size": 0, "size_human": "0 o",
                     "last_scan": None, "roots": [], "db_size": 0,
                     "error": str(exc)}
        recent = self.catalog.recent(limit=6) if stats.get("files") else []
        return {
            "version": VERSION,
            "app": APP_NAME,
            "stats": stats,
            "config": self.config,
            "recent": recent,
            "platform": sys.platform,
            "office": os.name == "nt",
            "data_dir": str(data_dir()),
            "cache_size": preview.cache_size(),
            "folders": _default_folders(),
        }

    # -- recherche ----------------------------------------------------------
    def search(self, query: str, groups: list[str] | None = None,
               sort: str = "relevance", limit: int = 120,
               folder: str | None = None) -> dict:
        try:
            return self.catalog.search(query or "", groups or [], limit, sort, folder)
        except Exception as exc:
            return {"error": str(exc), "files": [], "folders": [], "ms": 0,
                    "total_indexed": 0}

    def recent(self, groups: list[str] | None = None, limit: int = 6) -> list:
        try:
            return self.catalog.recent(groups, limit)
        except Exception:
            return []

    def preview(self, path: str) -> dict:
        try:
            return preview.render(path, allow_office=True)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def open_file(self, path: str) -> dict:
        if not Path(path).exists():
            return {"ok": False, "error": "Fichier introuvable"}
        try:
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
            return {"ok": True}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

    def reveal(self, path: str) -> dict:
        try:
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", path])
            else:
                subprocess.Popen(["xdg-open", str(Path(path).parent)])
            return {"ok": True}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

    def copy_path(self, path: str) -> dict:
        try:
            if os.name == "nt":
                subprocess.run("clip", input=path.encode("utf-16-le"), check=False)
            elif sys.platform == "darwin":
                subprocess.run("pbcopy", input=path.encode("utf-8"), check=False)
            else:
                subprocess.run(["xclip", "-selection", "clipboard"],
                               input=path.encode("utf-8"), check=False)
            return {"ok": True}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

    def open_folder(self, path: str) -> dict:
        return self.open_file(path if Path(path).is_dir() else str(Path(path).parent))

    # -- index --------------------------------------------------------------
    def choose_folders(self) -> list[str]:
        import webview

        if self._window is None:
            return []
        try:
            result = self._window.create_file_dialog(
                webview.FOLDER_DIALOG, allow_multiple=True
            )
        except Exception:
            return []
        if not result:
            return []
        return [str(Path(p)) for p in result]

    def start_scan(self, roots: list[str]) -> dict:
        with self._scan_lock:
            if self._scan["running"]:
                return {"ok": False, "error": "Un scan est déjà en cours"}
            roots = [str(Path(r)) for r in (roots or []) if r]
            if not roots:
                return {"ok": False, "error": "Choisis au moins un dossier"}
            missing = [r for r in roots if not Path(r).exists()]
            if missing:
                return {"ok": False, "error": "Introuvable : " + ", ".join(missing)}
            self._cancel.clear()
            self._scan = {
                "running": True, "phase": "scan", "files": 0, "dirs": 0,
                "added": 0, "visited": 0, "errors": 0, "excluded": 0,
                "current": "", "elapsed": 0, "result": None, "error": None,
            }
        self.config["roots"] = roots
        save_config(self.config)
        threading.Thread(target=self._scan_worker, args=(roots,), daemon=True,
                         name="scan").start()
        return {"ok": True}

    def _scan_worker(self, roots: list[str]) -> None:
        def progress(state: dict) -> None:
            with self._scan_lock:
                for key in ("files", "dirs", "added", "visited", "errors",
                            "excluded", "current", "elapsed"):
                    if key in state:
                        self._scan[key] = state[key]
                if state.get("done"):
                    self._scan["phase"] = "done"

        try:
            result = self.catalog.scan(roots, self.config, progress, self._cancel)
            with self._scan_lock:
                self._scan.update({
                    "running": False,
                    "phase": "done",
                    "result": result,
                    "files": result["files"],
                    "dirs": result["dirs"],
                    "current": "",
                })
        except Exception as exc:
            with self._scan_lock:
                self._scan.update({
                    "running": False, "phase": "error", "error": str(exc),
                })

    def scan_status(self) -> dict:
        with self._scan_lock:
            return dict(self._scan)

    def cancel_scan(self) -> dict:
        self._cancel.set()
        with self._scan_lock:
            self._scan["phase"] = "cancelling"
        return {"ok": True}

    def stats(self) -> dict:
        try:
            return self.catalog.stats()
        except Exception as exc:
            return {"error": str(exc), "files": 0, "dirs": 0, "size_human": "0 o"}

    # -- réglages -----------------------------------------------------------
    def get_config(self) -> dict:
        return self.config

    def save_config(self, patch: dict) -> dict:
        allowed = {"roots", "exclude_dirs", "include_exts", "threads"}
        for key, value in (patch or {}).items():
            if key in allowed:
                self.config[key] = value
        save_config(self.config)
        return self.config

    def cache_info(self) -> dict:
        size = preview.cache_size()
        return {"bytes": size, "human": human_size(size)}

    def clear_cache(self) -> dict:
        size = preview.clear_cache()
        return {"ok": True, "freed": human_size(size)}

    def open_data_dir(self) -> dict:
        return self.open_file(str(data_dir()))

    def check_paths(self, roots: list[str]) -> list[dict]:
        return [{"path": r, "exists": Path(r).exists()} for r in (roots or [])]


def _default_folders() -> list[str]:
    """Dossiers réseau probables, proposés au premier lancement."""
    guesses: list[str] = []
    if os.name == "nt":
        import string

        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                guesses.append(drive)
    return guesses


def main(debug: bool = False) -> int:
    import webview

    api = Api()
    index = ui_dir() / "index.html"
    if not index.exists():
        raise SystemExit(f"Interface introuvable : {index}")

    window = webview.create_window(
        APP_NAME,
        url=str(index),
        js_api=api,
        width=1360,
        height=880,
        min_size=(1000, 680),
        background_color="#F2F6F5",
        text_select=True,
    )
    api.attach(window)
    webview.start(debug=debug, private_mode=False)
    return 0
