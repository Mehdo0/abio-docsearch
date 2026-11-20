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

from .paths import human_size

SCHEMA_VERSION = "3.1"

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
CREATE INDEX IF NOT EXISTS idx_dirs_status ON folders(status);
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
        version = con.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()
        if not version or version[0] != SCHEMA_VERSION:
            # les index v2 n'ont pas la forme découpée des noms
            con.execute("DELETE FROM meta WHERE k='schema_version'")
            con.commit()
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
            con.execute("INSERT INTO fts(fts) VALUES('rebuild')")
            con.execute("INSERT INTO fts_dirs(fts_dirs) VALUES('rebuild')")
            con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('schema_version',?)",
                        (SCHEMA_VERSION,))
            con.commit()
    return con


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
) -> Iterable[tuple]:
    """Rend les entrées au fil de l'eau, sans aucun ``open()``."""
    out: queue.Queue = queue.Queue(maxsize=20000)
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
            try:
                with os.scandir(folder) as it:
                    for entry in it:
                        if cancel is not None and cancel.is_set():
                            break
                        try:
                            is_dir = entry.is_dir(follow_symlinks=False)
                        except OSError:
                            is_dir = False
                        if is_dir:
                            if entry.name.startswith(".") or norm(entry.name) in excluded_names:
                                skipped.append(1)
                                continue
                            with lock:
                                key = entry.path.lower()
                                if key in seen_dirs or depth > 60:
                                    continue
                                seen_dirs.add(key)
                                active[0] += 1
                            on_dir()
                            items.append(("d", entry.path, entry.name, depth + 1))
                            work.put((entry.path, depth + 1))
                        else:
                            on_tick(1)
                            try:
                                st = entry.stat(follow_symlinks=False)
                                size, mtime = st.st_size, st.st_mtime
                                attrs = getattr(st, "st_file_attributes", 0)
                                kind = "link" if attrs & 0x400 else "file"
                            except OSError:
                                size, mtime, kind = 0, 0, "file"
                            items.append(("f", entry.path, entry.name, size, mtime, kind))
            except PermissionError:
                errors.append((folder, "acces refuse"))
            except OSError as exc:
                errors.append((folder, str(exc)))
            finally:
                with lock:
                    active[0] -= 1
            if items:
                out.put(items)
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
            items = out.get(timeout=0.5)
        except queue.Empty:
            if stop.is_set() or (cancel is not None and cancel.is_set()):
                break
            if not any(w.is_alive() for w in workers):
                break
            on_render("")
            continue
        for item in items:
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
        con = self.con()
        scan_id = int(time.time())
        con.execute("CREATE TEMP TABLE IF NOT EXISTS seen(path TEXT PRIMARY KEY) WITHOUT ROWID")
        con.execute("CREATE TEMP TABLE IF NOT EXISTS seen_dir(path TEXT PRIMARY KEY) WITHOUT ROWID")
        con.execute("DELETE FROM seen")
        con.execute("DELETE FROM seen_dir")

        excluded = {norm(x) for x in config.get("exclude_dirs", [])}
        threads = int(config.get("threads", 8) or 8)
        include = {e.lower() for e in config.get("include_exts", [])}
        exclude_exts = {
            (e.lower() if e.lower().startswith(".") else "." + e.lower())
            for e in config.get("exclude_exts", [])
        }
        exclude_names = [n.lower() for n in config.get("exclude_names", [])]

        errors: list = []
        skipped: list = []
        t0 = time.perf_counter()
        counters = {"files": 0, "dirs": 0, "added": 0, "visited": 0, "ignored": 0}
        last_emit = [0.0]
        current = [""]
        batch: list[tuple] = []
        folder_batch: list[tuple] = []
        last_flush = [time.perf_counter()]

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

        def flush(force: bool = False) -> None:
            if batch:
                con.executemany(
                    "INSERT OR IGNORE INTO seen(path) VALUES(?)", [(b[0],) for b in batch]
                )
                con.executemany(
                    "INSERT INTO docs(path,name,body,ext,folder,size,mtime,scan_id,status) "
                    "VALUES(?,?,?,?,?,?,?,?, 'new') "
                    "ON CONFLICT(path) DO UPDATE SET size=excluded.size, "
                    "mtime=excluded.mtime, body=excluded.body "
                    "WHERE docs.size IS NOT excluded.size OR docs.mtime IS NOT excluded.mtime",
                    batch,
                )
                new_rows = con.execute(
                    "SELECT id,name,folder,coalesce(body,'') FROM docs WHERE status='new'"
                ).fetchall()
                if new_rows:
                    con.executemany(
                        "INSERT INTO fts(rowid,name,folder,body) VALUES(?,?,?,?)",
                        [(r[0], r[1], r[2] or "", r[3]) for r in new_rows],
                    )
                    con.execute("UPDATE docs SET status='ok' WHERE status='new'")
                counters["added"] += len(new_rows)
                counters["visited"] += len(batch)
                batch.clear()
            if folder_batch:
                con.executemany(
                    "INSERT OR IGNORE INTO seen_dir(path) VALUES(?)",
                    [(b[0],) for b in folder_batch],
                )
                con.executemany(
                    "INSERT INTO folders(path,name,depth,scan_id,status) VALUES(?,?,?,?, 'new') "
                    "ON CONFLICT(path) DO NOTHING",
                    folder_batch,
                )
                rows = con.execute("SELECT id,name FROM folders WHERE status='new'").fetchall()
                if rows:
                    con.executemany(
                        "INSERT INTO fts_dirs(rowid,name) VALUES(?,?)", rows
                    )
                    con.execute("UPDATE folders SET status='ok' WHERE status='new'")
                folder_batch.clear()
            con.commit()
            last_flush[0] = time.perf_counter()

        def on_tick(n: int) -> None:
            counters["files"] += n

        def on_dir() -> None:
            counters["dirs"] += 1

        def on_render(folder: str) -> None:
            if folder:
                current[0] = folder
            emit()

        cancelled = False
        try:
            for item in walk_paths(
                roots, excluded, threads, on_tick, on_dir, on_render,
                errors, skipped, cancel,
            ):
                if item[0] == "d":
                    _, path, name, depth = item
                    folder_batch.append((path, name, depth, scan_id))
                else:
                    _, path, name, size, mtime, kind = item
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
                    batch.append((path, name, expand_name(name), ext,
                                  os.path.dirname(path), size, mtime, scan_id))
                if len(batch) >= 8000:
                    flush()
                if time.perf_counter() - last_flush[0] > 20:
                    flush()
        except Exception as exc:  # pragma: no cover - disque débranché, etc.
            errors.append(("", str(exc)))
        finally:
            cancelled = bool(cancel is not None and cancel.is_set())
            flush(force=True)

        gone = 0
        if not cancelled:
            con.execute(
                "INSERT INTO fts(fts,rowid,name,folder,body) "
                "SELECT 'delete',id,name,folder,coalesce(body,'') FROM docs "
                "WHERE path NOT IN (SELECT path FROM seen)"
            )
            gone = con.execute(
                "SELECT count(*) FROM docs WHERE path NOT IN (SELECT path FROM seen)"
            ).fetchone()[0]
            con.execute("DELETE FROM docs WHERE path NOT IN (SELECT path FROM seen)")
            con.execute(
                "INSERT INTO fts_dirs(fts_dirs,rowid,name) SELECT 'delete',id,name "
                "FROM folders WHERE path NOT IN (SELECT path FROM seen_dir)"
            )
            con.execute("DELETE FROM folders WHERE path NOT IN (SELECT path FROM seen_dir)")
            con.commit()
            con.execute("INSERT INTO fts(fts) VALUES('optimize')")
            con.commit()
            con.execute(
                "INSERT OR REPLACE INTO meta(k,v) VALUES ('last_scan', ?)",
                (datetime.now().isoformat(timespec="seconds"),),
            )
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
            "gone": gone,
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

        rows: list[sqlite3.Row] = []
        if match:
            sql = """
                SELECT d.id, d.path, d.name, d.ext, d.folder, d.size, d.mtime,
                       snippet(fts, 0, '<<', '>>', ' … ', 10) AS snip,
                       bm25(fts, 20.0, 6.0, 1.0) AS score
                FROM fts JOIN docs d ON d.id = fts.rowid
                WHERE fts MATCH ?
            """
            params: list = [match]
            if ext_list:
                sql += " AND d.ext IN (%s)" % ",".join("?" * len(ext_list))
                params.extend(ext_list)
            if folder:
                sql += " AND d.folder LIKE ?"
                params.append(folder.rstrip("\\/") + "%")
            order = {
                "date": "d.mtime DESC",
                "name": "d.name COLLATE NOCASE ASC",
                "size": "d.size DESC",
                "relevance": "score ASC, d.mtime DESC",
            }.get(sort, "score ASC, d.mtime DESC")
            sql += f" ORDER BY {order} LIMIT ?"
            params.append(limit)
            try:
                rows = con.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                rows = []
        elif ext_list:
            order = {
                "date": "mtime DESC", "name": "name COLLATE NOCASE ASC",
                "size": "size DESC", "relevance": "mtime DESC",
            }.get(sort, "mtime DESC")
            sql = (
                "SELECT id,path,name,ext,folder,size,mtime,'' AS snip,0 AS score FROM docs "
                "WHERE ext IN (%s) ORDER BY %s LIMIT ?"
                % (",".join("?" * len(ext_list)), order)
            )
            rows = con.execute(sql, [*ext_list, limit]).fetchall()
        else:
            order = {
                "date": "mtime DESC", "name": "name COLLATE NOCASE ASC",
                "size": "size DESC", "relevance": "mtime DESC",
            }.get(sort, "mtime DESC")
            rows = con.execute(
                "SELECT id,path,name,ext,folder,size,mtime,'' AS snip,0 AS score "
                "FROM docs ORDER BY " + order + " LIMIT ?", (limit,)
            ).fetchall()

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
        return Path(path).exists()
