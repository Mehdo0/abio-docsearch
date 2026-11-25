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
from datetime import datetime, timedelta
from pathlib import Path

from . import preview
from .catalog import Catalog
from .paths import (
    APP_NAME,
    VERSION,
    data_dir,
    data_info,
    db_path,
    human_size,
    load_config,
    save_config,
    set_data_dir_pointer,
    ui_dir,
)
from .winpath import io_path


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
            "ignored": 0,
            "current": "",
            "elapsed": 0,
            "result": None,
            "error": None,
            "auto": False,
            "next_run": None,
        }
        self._cancel = threading.Event()
        self._auto_retry_at: datetime | None = None
        self._sched_stop = threading.Event()
        threading.Thread(target=self._schedule_loop, daemon=True,
                         name="auto-scan").start()

    def attach(self, window) -> None:
        self._window = window

    # -- bootstrap ----------------------------------------------------------
    def bootstrap(self) -> dict:
        try:
            stats = self.catalog.stats()
            stats_error = ""
        except Exception as exc:
            stats = {"files": 0, "dirs": 0, "size": 0, "size_human": "0 o",
                     "last_scan": None, "roots": [], "db_size": 0}
            stats_error = str(exc)
        recent = self.catalog.recent(limit=6) if stats.get("files") else []
        due = self._next_run_at()
        return {
            "version": VERSION,
            "app": APP_NAME,
            "stats": stats,
            "stats_error": stats_error,
            "data": data_info(),
            "config": self.config,
            "recent": recent,
            "platform": sys.platform,
            "office": preview.office_available(),
            "data_dir": str(data_dir()),
            "cache_size": preview.cache_size(),
            "folders": _default_folders(),
            "next_run": due.isoformat(timespec="seconds") if due else None,
        }

    # -- recherche ----------------------------------------------------------
    def search(self, query: str, groups: list[str] | None = None,
               sort: str = "relevance", limit: int = 120,
               folder: str | None = None, offset: int = 0) -> dict:
        try:
            return self.catalog.search(query or "", groups or [], limit, sort,
                                       folder, None, offset)
        except Exception as exc:
            return {"error": str(exc), "files": [], "folders": [], "ms": 0,
                    "total": 0, "total_indexed": 0}

    def preview_page(self, path: str, page: int = 1, zoom: float = 1.0) -> dict:
        try:
            return preview.render_page(path, page, zoom)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def retry_office(self) -> dict:
        """Nouvelle tentative après un échec de conversion Office."""
        preview.reset_office_failures()
        return {"ok": True, "office": preview.office_available()}

    def recent(self, groups: list[str] | None = None, limit: int = 6) -> list:
        try:
            return self.catalog.recent(groups, limit)
        except Exception:
            return []

    def preview(self, path: str, mode: str = "full") -> dict:
        try:
            return preview.render(path, allow_office=(mode != "fast"), mode=mode)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def preview_fast(self, path: str) -> dict:
        try:
            return preview.render(path, allow_office=False, mode="fast")
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def open_file(self, path: str) -> dict:
        target = io_path(path)
        if not Path(target).exists():
            return {"ok": False, "error": "Fichier introuvable"}
        if os.name == "nt":
            # Windows limite les chemins à 260 caractères. On essaie d'abord la
            # forme normale (comprise par toutes les applications), puis la
            # forme étendue, que le shell accepte sur les versions récentes.
            for candidate in (path, target):
                try:
                    os.startfile(candidate)  # type: ignore[attr-defined]
                    return {"ok": True}
                except OSError:
                    continue
            # Dernier recours : une copie temporaire, toujours ouvrable. Le
            # document reste consultable même si le chemin dépasse la limite.
            try:
                import hashlib
                import shutil
                import tempfile

                digest = hashlib.sha1(path.encode("utf-8", "replace")).hexdigest()[:8]
                copy = Path(tempfile.gettempdir()) / "Abionic" / f"{digest}_{Path(path).name}"
                copy.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(target, copy)
                os.startfile(str(copy))  # type: ignore[attr-defined]
                return {
                    "ok": True,
                    "note": "Chemin trop long : ouvert depuis une copie temporaire. "
                            "Les modifications ne seront pas enregistrées dans l'original.",
                }
            except OSError:
                return {
                    "ok": False,
                    "error": "Chemin trop long pour Windows. Utilisez « Dossier » "
                             "pour atteindre le document dans l'Explorateur.",
                }
        try:
            if sys.platform == "darwin":
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
        target = Path(io_path(path))
        return self.open_file(path if target.is_dir() else str(Path(path).parent))

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
        return self._start_scan(roots, auto=False)

    def _start_scan(self, roots: list[str], auto: bool = False) -> dict:
        with self._scan_lock:
            if self._scan["running"]:
                return {"ok": False, "error": "Un scan est déjà en cours"}
            roots = [str(Path(r)) for r in (roots or []) if r]
            if not roots:
                return {"ok": False, "error": "Choisis au moins un dossier"}
            missing = [r for r in roots if not Path(io_path(r)).exists()]
            if missing:
                return {"ok": False, "error": "Introuvable : " + ", ".join(missing)}
            self._cancel.clear()
            self._scan = {
                "running": True, "phase": "scan", "files": 0, "dirs": 0,
                "added": 0, "visited": 0, "errors": 0, "excluded": 0,
                "ignored": 0,
                "current": "", "elapsed": 0, "result": None, "error": None,
                "auto": auto, "next_run": None,
            }
        self.config["roots"] = roots
        save_config(self.config)
        threading.Thread(target=self._scan_worker, args=(roots,), daemon=True,
                         name="scan").start()
        return {"ok": True, "auto": auto}

    # -- mise à jour automatique ---------------------------------------------
    def _next_run_at(self) -> datetime | None:
        """Heure de la prochaine analyse automatique, ou None si désactivée."""
        if not self.config.get("auto_scan", True):
            return None
        if not (self.config.get("roots") or []):
            return None
        if self._auto_retry_at is not None:
            return self._auto_retry_at
        try:
            interval = int(self.config.get("scan_interval_min") or 30) * 60
        except (TypeError, ValueError):
            interval = 1800
        try:
            last = self.catalog.stats().get("last_attempt")
        except Exception:
            last = None
        if not last:
            return datetime.now()
        try:
            return datetime.fromisoformat(last) + timedelta(seconds=interval)
        except ValueError:
            return datetime.now()

    def _schedule_loop(self) -> None:
        # Laisse la fenêtre s'afficher avant de lancer quoi que ce soit.
        self._sched_stop.wait(15)
        while not self._sched_stop.is_set():
            try:
                self._auto_tick()
            except Exception:
                pass
            self._sched_stop.wait(15)

    def _auto_tick(self) -> None:
        with self._scan_lock:
            running = self._scan["running"]
        due = self._next_run_at()
        with self._scan_lock:
            self._scan["next_run"] = due.isoformat(timespec="seconds") if due else None
        if running or due is None or datetime.now() < due:
            return
        if self._auto_retry_at is not None:
            self._auto_retry_at = None
        started = self._start_scan(self.config.get("roots") or [], auto=True)
        if not started.get("ok"):
            # Partage débranché : on ne réessaie pas toutes les 15 secondes.
            self._auto_retry_at = datetime.now() + timedelta(minutes=10)
            with self._scan_lock:
                self._scan["next_run"] = self._auto_retry_at.isoformat(timespec="seconds")

    def _scan_worker(self, roots: list[str]) -> None:
        def progress(state: dict) -> None:
            with self._scan_lock:
                for key in ("files", "dirs", "added", "visited", "errors",
                            "excluded", "ignored", "current", "elapsed"):
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
        finally:
            self._auto_retry_at = None
            due = self._next_run_at()
            with self._scan_lock:
                self._scan["next_run"] = (
                    due.isoformat(timespec="seconds") if due else None
                )

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
        allowed = {"roots", "exclude_dirs", "include_exts", "exclude_exts",
                   "exclude_names", "threads", "auto_scan", "scan_interval_min"}
        for key, value in (patch or {}).items():
            if key in allowed:
                self.config[key] = value
        save_config(self.config)
        self.config = load_config()
        return self.config

    def cache_info(self) -> dict:
        size = preview.cache_size()
        return {"bytes": size, "human": human_size(size)}

    def clear_cache(self) -> dict:
        size = preview.clear_cache()
        return {"ok": True, "freed": human_size(size)}

    def open_data_dir(self) -> dict:
        return self.open_file(str(data_dir()))

    def data_info(self) -> dict:
        """Dossier de données : chemin retenu, origine, contenu, alertes."""
        return data_info()

    def copy_diagnostic(self) -> dict:
        """Copie un rapport lisible, à coller dans un message d'assistance."""
        info = data_info()
        stats = self.stats()
        lines = [
            f"{APP_NAME} {VERSION}",
            f"Système        : {sys.platform}",
            f"Exécutable     : {info.get('exe_dir') or '(sources)'}",
            f"Dossier données: {info['path']}",
            f"Origine        : {info['source']}",
            f"Index présent  : {'oui' if info['has_index'] else 'non'}",
            f"Index          : {info['index_human']}",
            f"Inscriptible   : {'oui' if info['writable'] else 'non'}",
            f"Documents      : {stats.get('files', 0)}",
            f"Dossiers       : {stats.get('dirs', 0)}",
            f"Dernier scan   : {stats.get('last_scan') or 'jamais'}",
            f"Racines        : {', '.join(stats.get('roots') or []) or '(aucune)'}",
            f"Pointeur       : {info['pointer']}",
            f"Cache aperçus  : {human_size(info['previews_bytes'])}",
        ]
        for warning in info["warnings"]:
            lines.append(f"ALERTE         : {warning}")
        for candidate in info["candidates"]:
            lines.append(
                f"Candidat       : {candidate['label']} — {candidate['path']} "
                f"— index={'oui' if candidate['has_index'] else 'non'} "
                f"({human_size(candidate['bytes'])})"
            )
        return self.copy_path("\n".join(lines))

    def open_scan_log(self) -> dict:
        log = data_dir() / "scan-errors.log"
        if not log.exists():
            return {"ok": False, "error": "Aucun journal : aucune erreur d'analyse enregistrée."}
        return self.open_file(str(log))

    def choose_data_dir(self) -> dict:
        """Indique où se trouve une bibliothèque déjà constituée."""
        import webview

        if self._window is None:
            return {"ok": False, "error": "Fenêtre indisponible"}
        try:
            picks = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if not picks:
            return {"ok": False, "cancelled": True}
        choisi = Path(picks[0] if isinstance(picks, (list, tuple)) else picks)
        found = _library_root(choisi)
        if found is None:
            return {"ok": False,
                    "error": "Aucun index (index.db) dans ce dossier. Choisissez "
                             "le dossier qui contient index.db, ou son parent."}
        try:
            set_data_dir_pointer(found)
        except OSError as exc:
            return {"ok": False, "error": f"Dossier non inscriptible : {exc}"}
        try:
            self.catalog.close()
        except Exception:
            pass
        self.catalog = Catalog(db_path())
        self.config = load_config()
        info = data_info()
        return {"ok": True, "data_dir": str(data_dir()), "data": info,
                "stats": self.catalog.stats()}

    def check_paths(self, roots: list[str]) -> list[dict]:
        return [{"path": r, "exists": Path(io_path(r)).exists()} for r in (roots or [])]


def _library_root(path: Path) -> Path | None:
    """Dossier contenant index.db, à la racine choisie ou dans son data/."""
    if (path / "index.db").is_file():
        return path
    if (path / "data" / "index.db").is_file():
        return path / "data"
    return None


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
