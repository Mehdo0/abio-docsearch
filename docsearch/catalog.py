"""Catalogue SQLite du partage : parcours des noms, index FTS5, recherche.

Aucun fichier n'est ouvert pendant le scan : ``os.scandir`` renvoie le nom,
la taille et la date dans la même réponse SMB. Le contenu des documents est
lu uniquement au moment d'un aperçu, à la demande.
"""

from __future__ import annotations

import fnmatch
import json
import os
import queue
import re
import sqlite3
import threading
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from .paths import as_list, human_size
from .winpath import display_path, io_path

SCHEMA_VERSION = "3.2"

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    id      INTEGER PRIMARY KEY,
    path    TEXT UNIQUE NOT NULL,
    name    TEXT NOT NULL,
    ext     TEXT,
    folder  TEXT,
    size    INTEGER,
    mtime   REAL,
    body    TEXT,
    status  TEXT DEFAULT 'new',
    scan_id INTEGER
);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
    name, folder, body,
    content='docs', content_rowid='id',
    tokenize="unicode61 remove_diacritics 2"
);
CREATE TABLE IF NOT EXISTS folders (
    id      INTEGER PRIMARY KEY,
    path    TEXT UNIQUE NOT NULL,
    name    TEXT NOT NULL,
    depth   INTEGER,
    parent  TEXT,
    status  TEXT DEFAULT 'new',
    scan_id INTEGER
);
CREATE VIRTUAL TABLE IF NOT EXISTS fts_dirs USING fts5(
    name, content='folders', content_rowid='id',
    tokenize="unicode61 remove_diacritics 2"
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX IF NOT EXISTS idx_docs_status ON docs(status);
CREATE INDEX IF NOT EXISTS idx_docs_scan ON docs(scan_id);
CREATE INDEX IF NOT EXISTS idx_docs_ext ON docs(ext);
CREATE INDEX IF NOT EXISTS idx_docs_mtime ON docs(mtime DESC);
CREATE INDEX IF NOT EXISTS idx_docs_folder ON docs(folder, path);
CREATE INDEX IF NOT EXISTS idx_dirs_status ON folders(status);
CREATE INDEX IF NOT EXISTS idx_dirs_parent ON folders(parent, path);
"""

TYPE_GROUPS: dict[str, tuple[str, ...]] = {
    "pdf": (".pdf",),
    "word": (".doc", ".docx", ".odt", ".rtf"),
    "excel": (".xls", ".xlsx", ".xlsm", ".csv", ".ods"),
    "ppt": (".ppt", ".pptx", ".odp"),
    "image": (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"),
    "text": (".txt", ".md", ".log", ".ini", ".xml", ".json"),
}

EXT_KIND = {ext: kind for kind, exts in TYPE_GROUPS.items() for ext in exts}


def kind_for(ext: str | None) -> str:
    return EXT_KIND.get((ext or "").lower(), "other")


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def expand_name(name: str) -> str:
    """Ajoute des séparations aux frontières de mots (abioCALIB -> abio CALIB).

    Le nom d'origine est conservé pour que la recherche exacte continue de
    fonctionner ; la variante découpée permet de trouver « calib ».
    """
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
    spaced = re.sub(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", spaced)
    return name if spaced == name else f"{name} {spaced}"


_migrate_lock = threading.Lock()


def open_db(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA cache_size=-64000")
    con.execute("PRAGMA temp_store=MEMORY")
    con.executescript(SCHEMA)
    con.commit()
    with _migrate_lock:
        _migrate(con)
    return con


def _version_tuple(value: str | None) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in str(value).split("."))
    except (TypeError, ValueError):
        return (0,)


def _migrate(con: sqlite3.Connection) -> None:
    """Met l'index à niveau, sans reconstruire ce qui n'a pas changé."""
    row = con.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()
    version = row[0] if row else None
    if version == SCHEMA_VERSION:
        return

    # v3.1 : les index v2 n'avaient pas la forme découpée des noms.
    if version is None or _version_tuple(version) < (3, 1):
        fixed = False
        cursor = con.execute("SELECT id, name FROM docs WHERE body IS NULL OR body=''")
        while True:
            rows = cursor.fetchmany(5000)
            if not rows:
                break
            con.executemany(
                "UPDATE docs SET body=? WHERE id=?",
                [(expand_name(r[1]), r[0]) for r in rows],
            )
            con.commit()
            fixed = True
        if fixed or con.execute("SELECT 1 FROM docs LIMIT 1").fetchone():
            con.execute("INSERT INTO fts(fts) VALUES('rebuild')")
            con.execute("INSERT INTO fts_dirs(fts_dirs) VALUES('rebuild')")

    # v3.2 : réconciliation par dossier. Le parent est stocké pour pouvoir
    # comparer les sous-dossiers d'un dossier sans balayer toute la base.
    columns = {r[1] for r in con.execute("PRAGMA table_info(folders)")}
    if "parent" not in columns:
        con.execute("ALTER TABLE folders ADD COLUMN parent TEXT")
    orphans = con.execute(
        "SELECT id, path FROM folders WHERE parent IS NULL"
    ).fetchall()
    if orphans:
        con.executemany(
            "UPDATE folders SET parent=? WHERE id=?",
            [(os.path.dirname(path), ident) for ident, path in orphans],
        )
    con.execute("CREATE INDEX IF NOT EXISTS idx_docs_folder ON docs(folder, path)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_dirs_parent ON folders(parent, path)")
    con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('schema_version',?)",
                (SCHEMA_VERSION,))
    con.commit()


def build_query(raw: str) -> str | None:
    """Chaque mot devient un préfixe, joints par AND. Les guillemets = phrase."""
    raw = raw.strip()
    if not raw:
        return None
    phrases = re.findall(r'"([^"]+)"', raw)
    rest = re.sub(r'"[^"]+"', " ", raw)
    terms = [t for t in re.split(r"[^\w\-]+", rest, flags=re.UNICODE) if t]
    parts = ['"' + p.replace('"', '""') + '"' for p in phrases]
    parts += ['"' + t.replace('"', '""') + '"*' for t in terms]
    return " AND ".join(parts) if parts else None


# ---------------------------------------------------------------------------
# parcours
# ---------------------------------------------------------------------------

def walk_paths(
    roots: Iterable[str],
    excluded_names: set[str],
    threads: int,
    on_tick: Callable[[int], None],
    on_dir: Callable[[], None],
    on_render: Callable[[str], None],
    errors: list,
    skipped: list,
    cancel: threading.Event | None = None,
    failed_roots: list | None = None,
) -> Iterable[tuple[str, int, bool, list[tuple]]]:
    """Rend un lot par dossier : (dossier, profondeur, lisible, entrées).

    Les entrées d'un dossier arrivent groupées : c'est ce qui permet de
    comparer ce dossier avec ce que l'index connaît de lui, et donc de retirer
    les fichiers disparus sans balayer tout l'index.

    ``lisible`` vaut False quand le dossier n'a pas pu être listé en entier
    (droits, partage coupé, arrêt demandé) : dans ce cas l'appelant ne doit
    rien supprimer pour ce dossier.
    """
    out: queue.Queue = queue.Queue(maxsize=4000)
    work: queue.Queue = queue.Queue()
    seen_dirs: set[str] = set()
    lock = threading.Lock()
    active = [0]
    stop = threading.Event()

    def worker() -> None:
        while not stop.is_set():
            try:
                folder, depth = work.get(timeout=0.4)
            except queue.Empty:
                with lock:
                    if active[0] == 0:
                        return
                continue
            items: list[tuple] = []
            ok = True
            try:
                # Préfixe étendu : sans lui, un dossier dont le chemin dépasse
                # 260 caractères est invisible pour Windows.
                with os.scandir(io_path(folder)) as it:
                    for entry in it:
                        if cancel is not None and cancel.is_set():
                            ok = False
                            break
                        child = display_path(os.path.join(folder, entry.name))
                        try:
                            is_dir = entry.is_dir(follow_symlinks=False)
                        except OSError:
                            is_dir = False
                        if is_dir:
                            if entry.name.startswith(".") or norm(entry.name) in excluded_names:
                                skipped.append(1)
                                continue
                            with lock:
                                key = child.lower()
                                if key in seen_dirs or depth > 60:
                                    continue
                                seen_dirs.add(key)
                                active[0] += 1
                            on_dir()
                            items.append(("d", child, entry.name))
                            work.put((child, depth + 1))
                        else:
                            on_tick(1)
                            try:
                                st = entry.stat(follow_symlinks=False)
                                size, mtime = st.st_size, st.st_mtime
                                attrs = getattr(st, "st_file_attributes", 0)
                                kind = "link" if attrs & 0x400 else "file"
                            except OSError:
                                size, mtime, kind = 0, 0, "file"
                            items.append(("f", child, entry.name, size, mtime, kind))
            except PermissionError:
                ok = False
                errors.append((folder, "acces refuse"))
            except OSError as exc:
                ok = False
                errors.append((folder, str(exc)))
            finally:
                with lock:
                    active[0] -= 1
            if not ok and depth == 0 and failed_roots is not None:
                failed_roots.append(folder)
            out.put((folder, depth, ok, items))
            on_render(folder)

    for root in roots:
        work.put((root, 0))
        with lock:
            active[0] += 1
    on_render(next(iter(roots), ""))

    workers = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, threads))]
    for w in workers:
        w.start()

    while True:
        try:
            item = out.get(timeout=0.5)
        except queue.Empty:
            if stop.is_set() or (cancel is not None and cancel.is_set()):
                break
            if not any(w.is_alive() for w in workers):
                break
            on_render("")
            continue
        yield item
    stop.set()
    for w in workers:
        w.join(timeout=3)


# ---------------------------------------------------------------------------
# catalogue
# ---------------------------------------------------------------------------

class Catalog:
    def __init__(self, db_file: str | Path):
        self.db_file = Path(db_file)
        self._local = threading.local()

    # -- connexion par thread ------------------------------------------------
    def con(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = open_db(self.db_file)
            self._local.con = con
        return con

    def close(self) -> None:
        con = getattr(self._local, "con", None)
        if con is not None:
            con.close()
            self._local.con = None

    # -- scan ----------------------------------------------------------------
    def scan(
        self,
        roots: list[str],
        config: dict,
        progress: Callable[[dict], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> dict:
        """Parcourt les racines et met l'index à jour dossier par dossier.

        Chaque dossier listé est comparé à ce que l'index connaît de lui : les
        fichiers absents du disque sont retirés tout de suite. Un dossier
        illisible n'est jamais réconcilié, donc une coupure réseau ne peut pas
        vider l'index.
        """
        con = self.con()
        scan_id = int(time.time())

        excluded = {norm(x) for x in as_list(config.get("exclude_dirs"))}
        threads = max(1, min(64, int(config.get("threads", 24) or 24)))
        include = {
            (e.lower() if e.lower().startswith(".") else "." + e.lower())
            for e in as_list(config.get("include_exts"))
        }
        exclude_exts = {
            (e.lower() if e.lower().startswith(".") else "." + e.lower())
            for e in as_list(config.get("exclude_exts"))
        }
        exclude_names = [n.lower() for n in as_list(config.get("exclude_names"))]

        errors: list = []
        skipped: list = []
        failed_roots: list[str] = []
        t0 = time.perf_counter()
        counters = {"files": 0, "dirs": 0, "added": 0, "visited": 0,
                    "ignored": 0, "gone": 0}
        last_emit = [0.0]
        last_flush = [time.perf_counter()]
        current = [""]

        def emit(force: bool = False) -> None:
            if progress is None:
                return
            now = time.perf_counter()
            if not force and now - last_emit[0] < 0.25:
                return
            last_emit[0] = now
            progress({
                "files": counters["files"],
                "dirs": counters["dirs"],
                "added": counters["added"],
                "visited": counters["visited"],
                "errors": len(errors),
                "excluded": len(skipped),
                "ignored": counters["ignored"],
                "current": current[0],
                "elapsed": round(now - t0, 1),
            })

        def commit_if_due(force: bool = False) -> None:
            if force or time.perf_counter() - last_flush[0] > 5:
                con.commit()
                last_flush[0] = time.perf_counter()

        def new_rows() -> None:
            """Ajoute à l'index de recherche les lignes qui viennent d'arriver."""
            rows = con.execute(
                "SELECT id,name,folder,coalesce(body,'') FROM docs WHERE status='new'"
            ).fetchall()
            if not rows:
                return
            con.executemany(
                "INSERT INTO fts(rowid,name,folder,body) VALUES(?,?,?,?)",
                [(r[0], r[1], r[2] or "", r[3]) for r in rows],
            )
            con.execute("UPDATE docs SET status='ok' WHERE status='new'")
            counters["added"] += len(rows)

        def drop_docs(paths: list[str]) -> None:
            for start in range(0, len(paths), 400):
                chunk = paths[start:start + 400]
                marks = ",".join("?" * len(chunk))
                rows = con.execute(
                    "SELECT id,name,folder,coalesce(body,'') FROM docs "
                    f"WHERE path IN ({marks})", chunk,
                ).fetchall()
                if not rows:
                    continue
                con.executemany(
                    "INSERT INTO fts(fts,rowid,name,folder,body) "
                    "VALUES('delete',?,?,?,?)",
                    [(r[0], r[1], r[2] or "", r[3]) for r in rows],
                )
                con.execute(f"DELETE FROM docs WHERE path IN ({marks})", chunk)
                counters["gone"] += len(rows)

        def drop_subtree(folder: str) -> None:
            """Retire un dossier supprimé : lui, tout ce qu'il contient, ses enfants."""
            prefix = folder + os.sep
            upper = prefix + "\uffff"
            where = "(path=? OR (path>=? AND path<?))"

            rows = con.execute(
                "SELECT id,name,folder,coalesce(body,'') FROM docs WHERE " + where,
                (folder, prefix, upper),
            ).fetchall()
            if rows:
                con.executemany(
                    "INSERT INTO fts(fts,rowid,name,folder,body) "
                    "VALUES('delete',?,?,?,?)",
                    [(r[0], r[1], r[2] or "", r[3]) for r in rows],
                )
                con.execute("DELETE FROM docs WHERE " + where, (folder, prefix, upper))
                counters["gone"] += len(rows)
            dirs = con.execute(
                "SELECT id,name FROM folders WHERE " + where, (folder, prefix, upper)
            ).fetchall()
            if dirs:
                con.executemany(
                    "INSERT INTO fts_dirs(fts_dirs,rowid,name) VALUES('delete',?,?)",
                    [(r[0], r[1]) for r in dirs],
                )
                con.execute("DELETE FROM folders WHERE " + where, (folder, prefix, upper))

        def sync_dir(folder: str, depth: int, entries: list[tuple]) -> None:
            files: list[tuple] = []
            children: list[tuple] = []
            for entry in entries:
                if entry[0] == "d":
                    children.append(entry)
                    continue
                _, path, name, size, mtime, _kind = entry
                ext = os.path.splitext(name)[1].lower()
                lower = name.lower()
                if include and ext not in include:
                    counters["ignored"] += 1
                    continue
                if ext in exclude_exts or lower.startswith("~$") or any(
                    fnmatch.fnmatch(lower, pattern) for pattern in exclude_names
                ):
                    counters["ignored"] += 1
                    continue
                files.append((path, name, expand_name(name), ext, folder,
                              size, mtime, scan_id))

            if files:
                con.executemany(
                    "INSERT INTO docs(path,name,body,ext,folder,size,mtime,scan_id,status) "
                    "VALUES(?,?,?,?,?,?,?,?, 'new') "
                    "ON CONFLICT(path) DO UPDATE SET size=excluded.size, "
                    "mtime=excluded.mtime, body=excluded.body "
                    "WHERE docs.size IS NOT excluded.size OR docs.mtime IS NOT excluded.mtime",
                    files,
                )
                counters["visited"] += len(files)
                new_rows()

            if children:
                con.executemany(
                    "INSERT INTO folders(path,name,depth,parent,scan_id,status) "
                    "VALUES(?,?,?,?,?, 'new') "
                    "ON CONFLICT(path) DO UPDATE SET parent=excluded.parent, "
                    "depth=excluded.depth",
                    [(child[1], child[2], depth + 1, folder, scan_id)
                     for child in children],
                )
                rows = con.execute(
                    "SELECT id,name FROM folders WHERE status='new'"
                ).fetchall()
                if rows:
                    con.executemany(
                        "INSERT INTO fts_dirs(rowid,name) VALUES(?,?)",
                        [(r[0], r[1]) for r in rows],
                    )
                    con.execute("UPDATE folders SET status='ok' WHERE status='new'")

            # Fichiers disparus de CE dossier. Le dossier vient d'être listé en
            # entier : ce qui n'y est plus a bien été supprimé.
            keep = {item[0] for item in files}
            present = con.execute(
                "SELECT path FROM docs WHERE folder=?", (folder,)
            ).fetchall()
            stale = [row[0] for row in present if row[0] not in keep]
            if stale:
                drop_docs(stale)

            keep_dirs = {child[1] for child in children}
            stored_dirs = con.execute(
                "SELECT path FROM folders WHERE parent=?", (folder,)
            ).fetchall()
            for (path,) in stored_dirs:
                if path not in keep_dirs:
                    drop_subtree(path)

        def on_render(value: str) -> None:
            if value:
                current[0] = value
            emit()

        cancelled = False
        try:
            for folder, depth, ok, entries in walk_paths(
                roots, excluded, threads,
                lambda n: counters.__setitem__("files", counters["files"] + n),
                lambda: counters.__setitem__("dirs", counters["dirs"] + 1),
                on_render, errors, skipped, cancel, failed_roots,
            ):
                if not ok:
                    continue
                sync_dir(folder, depth, entries)
                commit_if_due()
                emit()
        except Exception as exc:  # pragma: no cover - disque débranché, etc.
            errors.append(("", str(exc)))
        finally:
            cancelled = bool(cancel is not None and cancel.is_set())
            con.commit()

        # Le compactage de l'index coûte cher sur un gros partage : une fois par
        # jour, ou après un vrai remue-ménage. Pas à chaque analyse.
        files_total = con.execute("SELECT count(*) FROM docs").fetchone()[0]
        meta = {k: v for k, v in con.execute("SELECT k,v FROM meta").fetchall()}
        moved = counters["added"] + counters["gone"]
        needs_optimize = not meta.get("last_optimize")
        if not needs_optimize:
            try:
                age = (datetime.now()
                       - datetime.fromisoformat(meta["last_optimize"])).total_seconds()
                needs_optimize = age > 86400
            except ValueError:
                needs_optimize = True
        if not needs_optimize and moved > max(5000, files_total // 20):
            needs_optimize = True
        if needs_optimize and not cancelled and files_total:
            con.execute("INSERT INTO fts(fts) VALUES('optimize')")
            con.execute("INSERT INTO fts_dirs(fts_dirs) VALUES('optimize')")
            con.execute(
                "INSERT OR REPLACE INTO meta(k,v) VALUES ('last_optimize', ?)",
                (datetime.now().isoformat(timespec="seconds"),),
            )

        now = datetime.now().isoformat(timespec="seconds")
        con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES ('last_attempt', ?)", (now,))
        if not failed_roots and not cancelled:
            con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES ('last_scan', ?)", (now,))
        con.execute(
            "INSERT OR REPLACE INTO meta(k,v) VALUES ('last_scan_roots', ?)",
            (json.dumps(roots, ensure_ascii=False),),
        )
        con.commit()

        elapsed = time.perf_counter() - t0
        stats = self.stats()
        result = {
            "cancelled": cancelled,
            "files": stats["files"],
            "dirs": stats["dirs"],
            "added": counters["added"],
            "visited": counters["visited"],
            "gone": counters["gone"],
            "purged": not cancelled and not failed_roots,
            "failed_roots": failed_roots,
            "excluded": len(skipped),
            "ignored": counters["ignored"],
            "errors": len(errors),
            "elapsed": round(elapsed, 1),
            "rate": round(counters["visited"] / max(elapsed, 0.001)),
            "db_size": self.db_file.stat().st_size if self.db_file.exists() else 0,
        }
        if errors:
            log_path = self.db_file.parent / "scan-errors.log"
            try:
                with log_path.open("w", encoding="utf-8") as fh:
                    for path, why in errors[:5000]:
                        fh.write(f"{path}\t{why}\n")
            except OSError:
                pass
        if progress is not None:
            progress({**result, "done": True, "current": ""})
        return result

    # -- lecture -------------------------------------------------------------
    def stats(self) -> dict:
        con = self.con()
        total, size = con.execute("SELECT count(*), coalesce(sum(size),0) FROM docs").fetchone()
        ndirs = con.execute("SELECT count(*) FROM folders").fetchone()[0]
        meta = {k: v for k, v in con.execute("SELECT k,v FROM meta").fetchall()}
        roots: list[str] = []
        try:
            roots = json.loads(meta.get("last_scan_roots") or "[]")
        except ValueError:
            roots = []
        db_bytes = 0
        for suffix in ("", "-wal"):
            candidate = Path(str(self.db_file) + suffix)
            if candidate.exists():
                db_bytes += candidate.stat().st_size
        return {
            "files": total,
            "dirs": ndirs,
            "size": size,
            "size_human": human_size(size),
            "last_scan": meta.get("last_scan"),
            "last_attempt": meta.get("last_attempt"),
            "last_optimize": meta.get("last_optimize"),
            "roots": roots,
            "db_size": db_bytes,
        }

    def search(
        self,
        query: str,
        groups: list[str] | None = None,
        limit: int = 120,
        sort: str = "relevance",
        folder: str | None = None,
        exts: list[str] | None = None,
        offset: int = 0,
    ) -> dict:
        con = self.con()
        t0 = time.perf_counter()
        ext_list: list[str] = []
        for group in groups or []:
            ext_list.extend(TYPE_GROUPS.get(group, ()))
        for ext in exts or []:
            ext_list.append(ext.lower())
        ext_list = list(dict.fromkeys(ext_list))
        match = build_query(query)
        offset = max(0, int(offset or 0))
        limit = max(1, min(1000, int(limit or 120)))

        # Même clause FROM/WHERE pour la page de résultats et le comptage :
        # le total annoncé correspond toujours à ce qui est filtrable.
        rows: list[sqlite3.Row] = []
        total = 0
        if match:
            base = "FROM fts JOIN docs d ON d.id = fts.rowid WHERE fts MATCH ?"
            params: list = [match]
            if ext_list:
                base += " AND d.ext IN (%s)" % ",".join("?" * len(ext_list))
                params.extend(ext_list)
            if folder:
                base += " AND d.folder LIKE ?"
                params.append(folder.rstrip("\\/") + "%")
            order = {
                "date": "d.mtime DESC",
                "name": "d.name COLLATE NOCASE ASC",
                "size": "d.size DESC",
                "relevance": "score ASC, d.mtime DESC",
            }.get(sort, "score ASC, d.mtime DESC")
            sql = (
                "SELECT d.id, d.path, d.name, d.ext, d.folder, d.size, d.mtime, "
                "snippet(fts, 0, '<<', '>>', ' … ', 10) AS snip, "
                "bm25(fts, 20.0, 6.0, 1.0) AS score " + base
                + f" ORDER BY {order} LIMIT ? OFFSET ?"
            )
            try:
                total = con.execute("SELECT count(*) " + base, params).fetchone()[0]
                rows = con.execute(sql, [*params, limit, offset]).fetchall()
            except sqlite3.OperationalError:
                total, rows = 0, []
        else:
            base = "FROM docs d"
            params = []
            clauses = []
            if ext_list:
                clauses.append("d.ext IN (%s)" % ",".join("?" * len(ext_list)))
                params.extend(ext_list)
            if folder:
                clauses.append("d.folder LIKE ?")
                params.append(folder.rstrip("\\/") + "%")
            if clauses:
                base += " WHERE " + " AND ".join(clauses)
            order = {
                "date": "d.mtime DESC", "name": "d.name COLLATE NOCASE ASC",
                "size": "d.size DESC", "relevance": "d.mtime DESC",
            }.get(sort, "d.mtime DESC")
            try:
                total = con.execute("SELECT count(*) " + base, params).fetchone()[0]
            except sqlite3.OperationalError:
                total = 0
            try:
                rows = con.execute(
                    "SELECT d.id,d.path,d.name,d.ext,d.folder,d.size,d.mtime,"
                    "'' AS snip,0 AS score " + base
                    + f" ORDER BY {order} LIMIT ? OFFSET ?",
                    [*params, limit, offset],
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []

        dirs = []
        if match:
            try:
                dirs = con.execute(
                    "SELECT d.path, d.name, d.depth FROM fts_dirs "
                    "JOIN folders d ON d.id = fts_dirs.rowid "
                    "WHERE fts_dirs MATCH ? ORDER BY bm25(fts_dirs, 1.0) LIMIT 10",
                    (match,),
                ).fetchall()
            except sqlite3.OperationalError:
                dirs = []

        return {
            "query": query,
            "folder": folder or "",
            "total": total,
            "offset": offset,
            "files": [
                {
                    "id": r["id"],
                    "path": r["path"],
                    "name": r["name"],
                    "ext": r["ext"],
                    "kind": kind_for(r["ext"]),
                    "folder": r["folder"],
                    "size": r["size"],
                    "size_human": human_size(r["size"]),
                    "mtime": r["mtime"],
                    "date": datetime.fromtimestamp(r["mtime"]).strftime("%d.%m.%Y")
                    if r["mtime"] else "",
                    "snip": (r["snip"] or "").replace("<<", "").replace(">>", ""),
                }
                for r in rows
            ],
            "folders": [
                {"path": r["path"], "name": r["name"], "depth": r["depth"]} for r in dirs
            ],
            "ms": round((time.perf_counter() - t0) * 1000, 1),
            "total_indexed": con.execute("SELECT count(*) FROM docs").fetchone()[0],
        }

    def recent(self, groups: list[str] | None = None, limit: int = 8) -> list[dict]:
        con = self.con()
        exts: list[str] = []
        for group in groups or []:
            exts.extend(TYPE_GROUPS.get(group, ()))
        sql = "SELECT path,name,ext,folder,size,mtime FROM docs"
        params: list = []
        if exts:
            sql += " WHERE ext IN (%s)" % ",".join("?" * len(exts))
            params.extend(exts)
        sql += " ORDER BY mtime DESC LIMIT ?"
        params.append(limit)
        rows = con.execute(sql, params).fetchall()
        return [
            {
                "path": r["path"],
                "name": r["name"],
                "ext": r["ext"],
                "kind": kind_for(r["ext"]),
                "folder": r["folder"],
                "size": r["size"],
                "size_human": human_size(r["size"]),
                "mtime": r["mtime"],
                "date": datetime.fromtimestamp(r["mtime"]).strftime("%d.%m.%Y")
                if r["mtime"] else "",
                "snip": "",
            }
            for r in rows
        ]

    def path_exists(self, path: str) -> bool:
        return Path(io_path(path)).exists()
