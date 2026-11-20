"""Aperçu de la première page d'un document.

PDF et images : rendu réel de la page par PyMuPDF.
Word / Excel / PowerPoint : conversion en PDF par l'application Office
installée (COM), puis rendu de la page 1. Si Office est absent ou refuse,
on reconstruit une page lisible à partir du texte extrait.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

from .paths import preview_dir

try:  # PyMuPDF >= 1.24
    import pymupdf as fitz  # type: ignore
except ImportError:  # pragma: no cover
    import fitz  # type: ignore

PREVIEW_WIDTH = 900
PREVIEW_MAX_HEIGHT = 1500
OFFICE_TIMEOUT = 25

WORD_EXTS = {".doc", ".docx", ".docm", ".rtf", ".odt"}
EXCEL_EXTS = {".xls", ".xlsx", ".xlsm", ".csv", ".ods"}
PPT_EXTS = {".ppt", ".pptx", ".pptm", ".odp"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"}
TEXT_EXTS = {".txt", ".md", ".log", ".ini", ".xml", ".json", ".yml", ".yaml"}

_com_lock = threading.Lock()
_com_failures: dict[str, int] = {}


def _app_family(ext: str) -> str:
    if ext in WORD_EXTS:
        return "word"
    if ext in EXCEL_EXTS:
        return "excel"
    return "ppt"


def _com_unavailable(ext: str) -> bool:
    return _com_failures.get(_app_family(ext), 0) >= 3


def _cache_key(path: str, stat: os.stat_result) -> str:
    raw = f"{path.lower()}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8", "replace")
    return hashlib.sha1(raw).hexdigest()


def _cache_paths(key: str) -> tuple[Path, Path]:
    shard = preview_dir() / key[:2]
    shard.mkdir(parents=True, exist_ok=True)
    return shard / f"{key}.jpg", shard / f"{key}.json"


def cache_size() -> int:
    total = 0
    for p in preview_dir().rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def clear_cache() -> int:
    size = cache_size()
    for p in preview_dir().iterdir():
        try:
            shutil.rmtree(p) if p.is_dir() else p.unlink()
        except OSError:
            pass
    return size


def _encode_jpeg(pix) -> tuple[str, int, int]:
    data = pix.tobytes("jpeg", jpg_quality=85)
    return base64.b64encode(data).decode("ascii"), pix.width, pix.height


def _pdf_page_one(path: str) -> dict:
    doc = fitz.open(path)
    try:
        if doc.needs_pass:
            return {"ok": False, "error": "Document protégé par mot de passe"}
        if doc.page_count == 0:
            return {"ok": False, "error": "Document vide"}
        page = doc[0]
        rect = page.rect
        zoom = PREVIEW_WIDTH / max(rect.width, 1)
        if rect.height * zoom > PREVIEW_MAX_HEIGHT:
            zoom = PREVIEW_MAX_HEIGHT / max(rect.height, 1)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        data, width, height = _encode_jpeg(pix)
        return {
            "ok": True,
            "kind": "image",
            "mime": "image/jpeg",
            "data": data,
            "width": width,
            "height": height,
            "source": "pdf",
        }
    finally:
        doc.close()


def _image_preview(path: str) -> dict:
    doc = fitz.open(path)
    try:
        page = doc[0]
        rect = page.rect
        zoom = PREVIEW_WIDTH / max(rect.width, 1)
        if rect.height * zoom > PREVIEW_MAX_HEIGHT:
            zoom = PREVIEW_MAX_HEIGHT / max(rect.height, 1)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        data, width, height = _encode_jpeg(pix)
        return {
            "ok": True, "kind": "image", "mime": "image/jpeg", "data": data,
            "width": width, "height": height, "source": "image",
        }
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Office -> PDF (COM Windows)
# ---------------------------------------------------------------------------

def _word_to_pdf(src: str, dst: str) -> None:
    import pythoncom  # type: ignore
    import win32com.client  # type: ignore

    pythoncom.CoInitialize()
    app = None
    try:
        app = win32com.client.DispatchEx("Word.Application")
        app.Visible = False
        app.DisplayAlerts = 0
        app.Options.SaveNormalPrompt = False
        doc = app.Documents.Open(src, ReadOnly=True, AddToRecentFiles=False, Visible=False)
        try:
            doc.ExportAsFixedFormat(dst, 17)  # wdExportFormatPDF
        finally:
            doc.Close(False)
    finally:
        if app is not None:
            try:
                app.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()


def _excel_to_pdf(src: str, dst: str) -> None:
    import pythoncom  # type: ignore
    import win32com.client  # type: ignore

    pythoncom.CoInitialize()
    app = None
    try:
        app = win32com.client.DispatchEx("Excel.Application")
        app.Visible = False
        app.DisplayAlerts = False
        wb = app.Workbooks.Open(src, ReadOnly=True, UpdateLinks=0)
        try:
            wb.ExportAsFixedFormat(0, dst)  # xlTypePDF
        finally:
            wb.Close(False)
    finally:
        if app is not None:
            try:
                app.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()


def _ppt_to_pdf(src: str, dst: str) -> None:
    import pythoncom  # type: ignore
    import win32com.client  # type: ignore

    pythoncom.CoInitialize()
    app = None
    try:
        app = win32com.client.DispatchEx("PowerPoint.Application")
        pres = app.Presentations.Open(src, ReadOnly=True, WithWindow=False)
        try:
            pres.SaveAs(dst, 32)  # ppSaveAsPDF
        finally:
            pres.Close()
    finally:
        if app is not None:
            try:
                app.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()


def _office_to_pdf(ext: str, src: str, dst: str) -> None:
    if ext in WORD_EXTS:
        _word_to_pdf(src, dst)
    elif ext in EXCEL_EXTS:
        _excel_to_pdf(src, dst)
    elif ext in PPT_EXTS:
        _ppt_to_pdf(src, dst)
    else:
        raise ValueError(f"format non pris en charge : {ext}")


def _convert_with_timeout(ext: str, src: str, dst: str, timeout: int = OFFICE_TIMEOUT) -> bool:
    if os.name != "nt" or _com_unavailable(ext):
        return False
    if not _com_lock.acquire(timeout=timeout + 5):
        return False
    done = threading.Event()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            _office_to_pdf(ext, src, dst)
        except BaseException as exc:  # COM lève des erreurs très variées
            errors.append(exc)
        finally:
            done.set()
            _com_lock.release()

    thread = threading.Thread(target=run, daemon=True, name="office-preview")
    thread.start()
    if not done.wait(timeout):
        _com_failures[_app_family(ext)] = 3
        return False
    if errors or not Path(dst).exists():
        family = _app_family(ext)
        _com_failures[family] = _com_failures.get(family, 0) + 1
        return False
    _com_failures[_app_family(ext)] = 0
    return True


# ---------------------------------------------------------------------------
# aperçu texte (reconstruction)
# ---------------------------------------------------------------------------

def _docx_text(path: str) -> tuple[str, str]:
    from docx import Document  # type: ignore

    doc = Document(path)
    title = ""
    lines: list[str] = []
    for para in doc.paragraphs:
        text = (para.text or "").strip()
        if not text:
            continue
        if not title:
            title = text
        lines.append(text)
        if len(" ".join(lines)) > 2600:
            break
    if not lines:
        for table in doc.tables[:2]:
            for row in table.rows[:14]:
                cells = [c.text.strip() for c in row.cells]
                lines.append("  |  ".join(c for c in cells if c))
        title = Path(path).stem
    return title, "\n".join(lines)


def _pptx_text(path: str) -> tuple[str, str]:
    from pptx import Presentation  # type: ignore

    pres = Presentation(path)
    lines: list[str] = []
    title = ""
    if len(pres.slides):
        slide = pres.slides[0]
        for shape in slide.shapes:
            if not getattr(shape, "has_text_frame", False):
                continue
            for para in shape.text_frame.paragraphs:
                text = "".join(run.text for run in para.runs).strip()
                if text:
                    if not title:
                        title = text
                    lines.append(text)
    return title or Path(path).stem, "\n".join(lines[:40])


def _xlsx_table(path: str) -> list[list[str]]:
    from openpyxl import load_workbook  # type: ignore

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[wb.sheetnames[0]]
        rows: list[list[str]] = []
        for index, row in enumerate(ws.iter_rows(min_row=1, max_row=20, max_col=6, values_only=True)):
            cells = ["" if v is None else str(v) for v in row]
            if any(cells):
                rows.append(cells)
            if len(rows) >= 16:
                break
        return rows
    finally:
        wb.close()


def _text_preview(ext: str, path: str) -> dict:
    title = Path(path).stem
    body = ""
    table: list[list[str]] = []
    note = ""
    try:
        if ext in {".docx", ".docm"}:
            title, body = _docx_text(path)
        elif ext in {".pptx", ".pptm"}:
            title, body = _pptx_text(path)
        elif ext in {".xlsx", ".xlsm", ".ods"}:
            table = _xlsx_table(path)
        elif ext in TEXT_EXTS:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                body = fh.read(4000)
            first = next((l.strip() for l in body.splitlines() if l.strip()), "")
            title = first[:90] or title
        else:
            note = "Aperçu indisponible pour ce format."
    except Exception as exc:
        note = f"Aperçu indisponible ({type(exc).__name__})."

    if os.name != "nt" and ext in (WORD_EXTS | EXCEL_EXTS | PPT_EXTS):
        note = note or "Rendu Office non disponible sur cette plateforme."
    return {
        "ok": True,
        "kind": "text",
        "source": ext.lstrip("."),
        "title": title,
        "text": body[:3000],
        "table": table,
        "note": note,
    }


# ---------------------------------------------------------------------------
# point d'entrée
# ---------------------------------------------------------------------------

def render(path: str, allow_office: bool = True) -> dict:
    started = time.perf_counter()
    try:
        st = os.stat(path)
    except OSError as exc:
        return {"ok": False, "error": f"Fichier introuvable ({exc.strerror})"}

    key = _cache_key(path, st)
    jpg_path, meta_path = _cache_paths(key)
    if meta_path.exists():
        try:
            cached = json.loads(meta_path.read_text(encoding="utf-8"))
            if cached.get("kind") == "image" and jpg_path.exists():
                cached["data"] = base64.b64encode(jpg_path.read_bytes()).decode("ascii")
                cached["cached"] = True
                cached["ms"] = round((time.perf_counter() - started) * 1000)
                return cached
            if cached.get("kind") == "text":
                cached["cached"] = True
                cached["ms"] = round((time.perf_counter() - started) * 1000)
                return cached
        except (OSError, ValueError):
            pass

    ext = Path(path).suffix.lower()
    result: dict
    if ext == ".pdf":
        try:
            result = _pdf_page_one(path)
        except Exception as exc:
            result = {"ok": False, "error": f"PDF illisible ({type(exc).__name__})"}
    elif ext in IMAGE_EXTS:
        try:
            result = _image_preview(path)
        except Exception as exc:
            result = {"ok": False, "error": f"Image illisible ({type(exc).__name__})"}
    elif ext in (WORD_EXTS | EXCEL_EXTS | PPT_EXTS):
        result = {"ok": False, "error": "Conversion Office impossible"}
        if allow_office and os.name == "nt":
            tmp_dir = Path(tempfile.mkdtemp(prefix="abiods-"))
            tmp_pdf = tmp_dir / "preview.pdf"
            try:
                if _convert_with_timeout(ext, path, str(tmp_pdf)):
                    result = _pdf_page_one(str(tmp_pdf))
                    result["source"] = ext.lstrip(".")
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)
        if not result.get("ok"):
            result = _text_preview(ext, path)
    else:
        result = _text_preview(ext, path)

    result["path"] = path
    result["name"] = Path(path).name
    result["ms"] = round((time.perf_counter() - started) * 1000)

    try:
        if result.get("kind") == "image":
            jpg_path.write_bytes(base64.b64decode(result["data"]))
            meta = {k: v for k, v in result.items() if k != "data"}
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
        elif result.get("ok"):
            meta_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return result


def warm(path: str) -> None:
    """Préchauffe le cache sans bloquer l'appelant."""
    threading.Thread(target=render, args=(path,), daemon=True).start()
