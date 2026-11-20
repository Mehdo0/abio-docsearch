"""Aperçu de la première page d'un document.

PDF et images : rendu réel de la page par PyMuPDF (repli Pillow pour les
formats que MuPDF refuse, comme le WebP).
Word / Excel / PowerPoint : conversion en PDF par l'application Office
installée (COM), puis rendu de la page 1. En mode rapide, ou si Office est
absent, on reconstruit une page lisible à partir du contenu extrait.

L'extraction Word lit directement `word/document.xml` : elle ne dépend ni des
styles, ni des relations, ni de `[Content_Types].xml`, qui font échouer
python-docx sur certains fichiers réparés par Word.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .paths import data_dir, load_config, preview_dir
from .winpath import io_path

try:  # PyMuPDF >= 1.24
    import pymupdf as fitz  # type: ignore
except ImportError:  # pragma: no cover
    import fitz  # type: ignore

PREVIEW_WIDTH = 900
PREVIEW_MAX_HEIGHT = 1500
OFFICE_TIMEOUT = 20
MAX_TABLE_ROWS = 16
MAX_TABLE_COLS = 6
TEXT_LIMIT = 2600

WORD_EXTS = {".doc", ".docx", ".docm", ".rtf", ".odt"}
EXCEL_EXTS = {".xls", ".xlsx", ".xlsm", ".csv", ".ods"}
PPT_EXTS = {".ppt", ".pptx", ".pptm", ".odp"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"}
TEXT_EXTS = {".txt", ".md", ".log", ".ini", ".xml", ".json", ".yml", ".yaml"}
OFFICE_EXTS = WORD_EXTS | EXCEL_EXTS | PPT_EXTS

_com_lock = threading.Lock()
_com_failures: dict[str, int] = {}
_cache_writes = 0
_logger: logging.Logger | None = None

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
S_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


# ---------------------------------------------------------------------------
# journalisation
# ---------------------------------------------------------------------------

def _log() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger
    logger = logging.getLogger("docsearch.preview")
    logger.setLevel(logging.WARNING)
    if not logger.handlers:
        try:
            handler = logging.FileHandler(data_dir() / "preview.log", encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(handler)
        except OSError:
            logger.addHandler(logging.NullHandler())
    _logger = logger
    return logger


def _report(path: str, exc: BaseException) -> None:
    try:
        _log().warning("aperçu impossible pour %r : %s", path, exc, exc_info=True)
    except Exception:
        pass


def _friendly_error(exc: BaseException) -> str:
    name = type(exc).__name__
    if isinstance(exc, zipfile.BadZipFile):
        return "Fichier endommagé, incomplet ou protégé par mot de passe."
    if name in {"PackageNotFoundError", "InvalidFileException"}:
        return "Format non reconnu ou fichier protégé par mot de passe."
    if name in {"KeyError", "XMLSyntaxError", "ParseError"}:
        return "Document incomplet : son contenu n'a pas pu être lu."
    if name in {"FileDataError", "EmptyFileError", "FileNotFoundError"}:
        return "Fichier illisible ou vide."
    return "Aperçu indisponible pour ce document."


# ---------------------------------------------------------------------------
# cache disque (jamais bloquant)
# ---------------------------------------------------------------------------

def _pdf_open(path: str):
    """Ouvre un PDF ou une image, y compris au-delà de 260 caractères.

    MuPDF reçoit parfois mal le préfixe étendu : en cas d'échec, les octets
    sont lus par Python (qui le gère) puis passés en mémoire.
    """
    target = io_path(path)
    try:
        return fitz.open(target)
    except Exception:
        # Un PDF de plusieurs centaines de Mo ne doit pas être chargé en RAM.
        if Path(target).stat().st_size > 300 * 1024 * 1024:
            raise
        return fitz.open(stream=Path(target).read_bytes())


def _cache_key(path: str, stat: os.stat_result) -> str:
    raw = f"{path.lower()}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8", "replace")
    return hashlib.sha1(raw).hexdigest()


def _cache_paths(key: str) -> tuple[Path, Path, Path] | None:
    try:
        shard = preview_dir() / key[:2]
        shard.mkdir(parents=True, exist_ok=True)
        return shard / f"{key}.jpg", shard / f"{key}.image.json", shard / f"{key}.text.json"
    except (OSError, ValueError):
        return None


def cache_size() -> int:
    total = 0
    try:
        for p in preview_dir().rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def clear_cache() -> int:
    size = cache_size()
    try:
        for p in preview_dir().iterdir():
            try:
                shutil.rmtree(p) if p.is_dir() else p.unlink()
            except OSError:
                pass
    except OSError:
        pass
    return size


def _prune_cache() -> None:
    """Applique preview_cache_mb en supprimant les fichiers les plus anciens."""
    try:
        limit = int(load_config().get("preview_cache_mb", 800)) * 1024 * 1024
    except Exception:
        limit = 800 * 1024 * 1024
    entries = []
    total = 0
    try:
        for p in preview_dir().rglob("*"):
            try:
                if p.is_file():
                    stat = p.stat()
                    entries.append((stat.st_mtime, stat.st_size, p))
                    total += stat.st_size
            except OSError:
                pass
    except OSError:
        return
    if total <= limit:
        return
    entries.sort()
    for _, size, p in entries:
        if total <= limit * 0.9:
            break
        try:
            p.unlink()
            total -= size
        except OSError:
            pass


def _load_cache(key: str, want_image: bool) -> dict | None:
    paths = _cache_paths(key)
    if paths is None:
        return None
    jpg_path, image_meta, text_meta = paths
    try:
        if want_image and image_meta.exists() and jpg_path.exists():
            raw = jpg_path.read_bytes()
            if not raw.startswith(b"\xff\xd8"):
                return None  # image de cache corrompue : on régénère
            cached = json.loads(image_meta.read_text(encoding="utf-8"))
            cached["data"] = base64.b64encode(raw).decode("ascii")
            return cached
        if not want_image and text_meta.exists():
            return json.loads(text_meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return None


def _store_cache(key: str, result: dict) -> None:
    global _cache_writes
    paths = _cache_paths(key)
    if paths is None:
        return
    jpg_path, image_meta, text_meta = paths
    has_content = bool(result.get("text") or result.get("table"))
    try:
        if result.get("kind") == "image":
            jpg_path.write_bytes(base64.b64decode(result["data"]))
            meta = {k: v for k, v in result.items() if k not in ("data", "cached", "ms")}
            image_meta.write_text(json.dumps(meta, ensure_ascii=True), encoding="utf-8")
        elif result.get("ok") and has_content:
            meta = {k: v for k, v in result.items() if k not in ("cached", "ms")}
            text_meta.write_text(json.dumps(meta, ensure_ascii=True), encoding="utf-8")
        else:
            return
    except (OSError, ValueError, KeyError):
        return
    _cache_writes += 1
    if _cache_writes % 20 == 0:
        _prune_cache()


# ---------------------------------------------------------------------------
# rendu des pages
# ---------------------------------------------------------------------------

def _encode_jpeg(pix) -> tuple[str, int, int]:
    data = pix.tobytes("jpeg", jpg_quality=82)
    return base64.b64encode(data).decode("ascii"), pix.width, pix.height


def _pdf_page_one(path: str) -> dict:
    doc = _pdf_open(path)
    try:
        if doc.needs_pass:
            return {"ok": False, "error": "Document protégé par mot de passe."}
        if doc.page_count == 0:
            return {"ok": False, "error": "Document vide."}
        page = doc[0]
        rect = page.rect
        zoom = PREVIEW_WIDTH / max(rect.width, 1)
        if rect.height * zoom > PREVIEW_MAX_HEIGHT:
            zoom = PREVIEW_MAX_HEIGHT / max(rect.height, 1)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        data, width, height = _encode_jpeg(pix)
        return {
            "ok": True, "kind": "image", "mime": "image/jpeg", "data": data,
            "width": width, "height": height, "source": "pdf",
        }
    finally:
        doc.close()


def _image_preview_pymupdf(path: str) -> dict:
    doc = _pdf_open(path)
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


def _image_preview_pillow(path: str) -> dict:
    from PIL import Image  # type: ignore

    with Image.open(io_path(path)) as image:
        image.seek(0)
        image = image.convert("RGB")
        image.thumbnail((PREVIEW_WIDTH, PREVIEW_MAX_HEIGHT))
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=82)
        width, height = image.size
    return {
        "ok": True, "kind": "image", "mime": "image/jpeg",
        "data": base64.b64encode(buffer.getvalue()).decode("ascii"),
        "width": width, "height": height, "source": "image",
    }


def _image_preview(path: str) -> dict:
    try:
        return _image_preview_pymupdf(path)
    except Exception:
        return _image_preview_pillow(path)


# ---------------------------------------------------------------------------
# Office -> PDF (COM Windows)
# ---------------------------------------------------------------------------

def _app_family(ext: str) -> str:
    if ext in WORD_EXTS:
        return "word"
    if ext in EXCEL_EXTS:
        return "excel"
    return "ppt"


def _com_unavailable(ext: str) -> bool:
    return _com_failures.get(_app_family(ext), 0) >= 3


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
    else:
        _ppt_to_pdf(src, dst)


def office_available() -> bool:
    return os.name == "nt" and not any(v >= 3 for v in _com_failures.values())


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
        _report(src, errors[0] if errors else RuntimeError("export PDF vide"))
        return False
    _com_failures[_app_family(ext)] = 0
    return True


# ---------------------------------------------------------------------------
# détection par signature
# ---------------------------------------------------------------------------

def _sniff_ext(path: str, ext: str) -> str:
    try:
        with open(io_path(path), "rb") as fh:
            head = fh.read(16)
    except OSError:
        return ext
    if head.startswith(b"%PDF"):
        return ".pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if head.startswith(b"BM"):
        return ".bmp"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return ".tif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head.startswith(b"PK\x03\x04") and ext not in OFFICE_EXTS:
        try:
            with zipfile.ZipFile(io_path(path)) as archive:
                names = archive.namelist()
            if "word/document.xml" in names:
                return ".docx"
            if "xl/workbook.xml" in names:
                return ".xlsx"
            if "ppt/presentation.xml" in names:
                return ".pptx"
        except (OSError, zipfile.BadZipFile):
            pass
    return ext


# ---------------------------------------------------------------------------
# texte
# ---------------------------------------------------------------------------

def _decode_bytes(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _clean_text(text: str) -> str:
    return "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 32)


def _clean_lines(lines: list[str], limit: int = TEXT_LIMIT) -> list[str]:
    out: list[str] = []
    size = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        out.append(line)
        size += len(line)
        if size > limit:
            break
    return out


# --- Word -------------------------------------------------------------------

def _docx_paragraph_text(node) -> str:
    parts: list[str] = []
    for child in node.iter():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "t" and child.text:
            parts.append(child.text)
        elif tag in ("delText", "instrText"):
            continue
        elif tag == "tab":
            parts.append("    ")
        elif tag in ("br", "cr"):
            parts.append("\n")
    return "".join(parts).strip()


def _docx_xml_text(path: str) -> tuple[str, list[str]]:
    """Lit word/document.xml sans styles, relations ni content-types.

    Gère les paragraphes, les tableaux, les zones de texte et les content
    controls (w:sdt), qui échappent à python-docx.
    """
    with zipfile.ZipFile(io_path(path)) as archive:
        if "word/document.xml" not in archive.namelist():
            raise ValueError("archive sans corps Word")
        xml = archive.read("word/document.xml")
    root = ET.fromstring(xml)
    body = root.find(f"{{{W_NS}}}body")
    if body is None:
        return Path(path).stem, []

    lines: list[str] = []
    tables = 0

    def add_table(table) -> None:
        nonlocal tables
        if tables >= 3:
            return
        tables += 1
        rows = table.findall(f".//{{{W_NS}}}tr")
        for row in rows[:MAX_TABLE_ROWS]:
            cells = []
            for cell in row.findall(f"{{{W_NS}}}tc"):
                cells.append(" ".join(filter(
                    None, (_docx_paragraph_text(p) for p in cell.findall(f".//{{{W_NS}}}p"))
                )))
            line = "  |  ".join(c for c in cells if c)
            if line:
                lines.append(line)
        if len(rows) > MAX_TABLE_ROWS:
            lines.append(f"… {len(rows) - MAX_TABLE_ROWS} lignes du tableau non affichées")

    def walk(node) -> None:
        for child in list(node):
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                text = _docx_paragraph_text(child)
                if text:
                    lines.append(text)
            elif tag == "tbl":
                add_table(child)
            elif tag == "sdt":
                content = child.find(f"{{{W_NS}}}sdtContent")
                if content is not None:
                    walk(content)

    walk(body)
    return (lines[0] if lines else Path(path).stem), _clean_lines(lines)


def _docx_text_pydocx(path: str) -> tuple[str, list[str]]:
    from docx import Document  # type: ignore

    doc = Document(io_path(path))
    lines = _clean_lines([(p.text or "").strip() for p in doc.paragraphs])
    if len(" ".join(lines)) < TEXT_LIMIT:
        for table in doc.tables[:3]:
            for row in table.rows[:MAX_TABLE_ROWS]:
                try:
                    cells = [(c.text or "").strip() for c in row.cells]
                except (IndexError, KeyError, ValueError):
                    continue
                line = "  |  ".join(c for c in cells if c)
                if line:
                    lines.append(line)
    return (lines[0] if lines else Path(path).stem), lines


def _docx_text(path: str) -> tuple[str, list[str], str]:
    try:
        title, lines = _docx_xml_text(path)
        return title, lines, "docx"
    except Exception as first:
        _report(path, first)
        title, lines = _docx_text_pydocx(path)
        return title, lines, "docx"


# --- PowerPoint -------------------------------------------------------------

def _pptx_walk_shapes(shapes, lines: list[str], depth: int = 0) -> None:
    if depth > 3:
        return
    for shape in shapes:
        try:
            if getattr(shape, "shape_type", None) == 6:  # MSO_SHAPE_TYPE.GROUP
                _pptx_walk_shapes(shape.shapes, lines, depth + 1)
                continue
            if getattr(shape, "has_table", False):
                for row in list(shape.table.rows)[:MAX_TABLE_ROWS]:
                    line = "  |  ".join((cell.text or "").strip() for cell in row.cells)
                    if line.strip(" |"):
                        lines.append(line)
                continue
            if getattr(shape, "has_text_frame", False):
                for para in shape.text_frame.paragraphs:
                    text = "".join(run.text for run in para.runs).strip()
                    if text:
                        lines.append(text)
        except Exception:
            continue


def _pptx_text_pydocx(path: str) -> tuple[str, list[str]]:
    from pptx import Presentation  # type: ignore

    pres = Presentation(io_path(path))
    lines: list[str] = []
    if len(pres.slides):
        _pptx_walk_shapes(pres.slides[0].shapes, lines)
    lines = _clean_lines(lines)
    return (lines[0] if lines else Path(path).stem), lines


def _pptx_xml_text(path: str) -> tuple[str, list[str]]:
    with zipfile.ZipFile(io_path(path)) as archive:
        names = sorted(n for n in archive.namelist()
                       if re.fullmatch(r"ppt/slides/slide\d+\.xml", n))
        if not names:
            return Path(path).stem, []
        xml = archive.read(names[0])
    root = ET.fromstring(xml)
    lines = [(node.text or "").strip() for node in root.iter(f"{{{A_NS}}}t")]
    lines = _clean_lines([line for line in lines if line])
    return (lines[0] if lines else Path(path).stem), lines


def _pptx_text(path: str) -> tuple[str, list[str], str]:
    try:
        title, lines = _pptx_text_pydocx(path)
        return title, lines, "pptx"
    except Exception as first:
        _report(path, first)
        title, lines = _pptx_xml_text(path)
        return title, lines, "pptx"


# --- Excel ------------------------------------------------------------------

def _cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _xlsx_table_openpyxl(path: str) -> tuple[list[list[str]], bool]:
    from openpyxl import load_workbook  # type: ignore

    def read(data_only: bool) -> tuple[list[list[str]], bool]:
        wb = load_workbook(io_path(path), read_only=True, data_only=data_only)
        try:
            ws = None
            for name in wb.sheetnames:
                sheet = wb[name]
                if getattr(sheet, "sheet_state", "visible") != "visible":
                    continue
                if not hasattr(sheet, "iter_rows"):
                    continue  # graphique (chartsheet)
                ws = sheet
                break
            if ws is None:
                return [], False
            rows: list[list[str]] = []
            truncated = False
            for row in ws.iter_rows(min_row=1, max_row=MAX_TABLE_ROWS + 1,
                                    max_col=MAX_TABLE_COLS + 1, values_only=True):
                cells = [_cell_text(v) for v in row]
                if len(rows) >= MAX_TABLE_ROWS:
                    if any(c.strip() for c in cells):
                        truncated = True
                    break
                if any(cells):
                    if any(c.strip() for c in cells[MAX_TABLE_COLS:]):
                        truncated = True
                    rows.append(cells[:MAX_TABLE_COLS])
            return rows, truncated
        finally:
            wb.close()

    rows, truncated = read(True)
    filled = sum(1 for row in rows for cell in row if cell.strip())
    if filled < 3:
        # Feuille très pauvre : souvent des formules sans valeur calculée.
        formulas, truncated_f = read(False)
        if sum(1 for row in formulas for cell in row if cell.strip()) > filled:
            return formulas, truncated_f
    return rows, truncated


def _xlsx_table_xml(path: str) -> list[list[str]]:
    with zipfile.ZipFile(io_path(path)) as archive:
        names = archive.namelist()
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.iter(f"{{{S_NS}}}si"):
                shared.append("".join(t.text or "" for t in item.iter(f"{{{S_NS}}}t")))
        sheets = sorted(n for n in names
                        if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
        if not sheets:
            return []
        root = ET.fromstring(archive.read(sheets[0]))
    rows: list[list[str]] = []
    for row in root.iter(f"{{{S_NS}}}row"):
        values: dict[int, str] = {}
        for cell in row.iter(f"{{{S_NS}}}c"):
            ref = cell.get("r") or ""
            letters = re.match(r"[A-Z]+", ref)
            if not letters:
                continue
            column = 0
            for char in letters.group(0):
                column = column * 26 + (ord(char) - 64)
            column -= 1
            if column >= MAX_TABLE_COLS:
                continue
            kind = cell.get("t")
            value_node = cell.find(f"{{{S_NS}}}v")
            text = ""
            if kind == "s" and value_node is not None:
                index = int(value_node.text or 0)
                text = shared[index] if index < len(shared) else ""
            elif kind == "inlineStr":
                text = "".join(t.text or "" for t in cell.iter(f"{{{S_NS}}}t"))
            elif value_node is not None:
                text = value_node.text or ""
            if text:
                values[column] = text
        if values:
            rows.append([values.get(i, "") for i in range(max(values) + 1)])
        if len(rows) >= MAX_TABLE_ROWS:
            break
    return rows


def _xlsx_table(path: str) -> tuple[list[list[str]], bool]:
    try:
        rows, truncated = _xlsx_table_openpyxl(path)
        if rows:
            return rows, truncated
    except Exception as first:
        _report(path, first)
    return _xlsx_table_xml(path), False


def _csv_table(path: str) -> list[list[str]]:
    raw = Path(io_path(path)).read_bytes()[:400_000]
    text = _decode_bytes(raw)
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows: list[list[str]] = []
    for row in csv.reader(io.StringIO(text), dialect):
        if len(rows) >= MAX_TABLE_ROWS:
            break
        cells = [cell.strip() for cell in row[:MAX_TABLE_COLS]]
        if any(cells):
            rows.append(cells)
    return rows


# --- texte brut -------------------------------------------------------------

def _text_preview(ext: str, path: str) -> dict:
    title = Path(path).stem
    body = ""
    table: list[list[str]] = []
    note = ""
    source = ext.lstrip(".")
    truncated = False
    try:
        if ext in {".docx", ".docm"}:
            title, lines, source = _docx_text(path)
            body = "\n".join(lines)
        elif ext in {".pptx", ".pptm"}:
            title, lines, source = _pptx_text(path)
            body = "\n".join(lines)
        elif ext in {".xlsx", ".xlsm"}:
            table, truncated = _xlsx_table(path)
        elif ext == ".csv":
            table = _csv_table(path)
        elif ext in TEXT_EXTS:
            body = _clean_text(_decode_bytes(Path(io_path(path)).read_bytes()[:200_000]))
            first = next((line.strip() for line in body.splitlines() if line.strip()), "")
            title = first[:90] or title
            body = body[:TEXT_LIMIT]
        elif ext in {".doc", ".xls", ".ppt", ".rtf", ".odt", ".ods", ".odp"}:
            note = "Format ancien : l'aperçu complet nécessite Microsoft Office."
        else:
            note = "Aperçu indisponible pour ce format."
    except Exception as exc:
        _report(path, exc)
        note = _friendly_error(exc)

    if truncated:
        note = (note + " " if note else "") + "Aperçu limité aux 16 premières lignes et 6 colonnes."
    if not body and not table and not note:
        note = "Aucun texte détecté : document vide, scanné ou composé d'images."
    if os.name != "nt" and ext in OFFICE_EXTS and ext != ".csv" and not note:
        note = "Rendu Office non disponible sur cette plateforme."
    return {
        "ok": True,
        "kind": "text",
        "source": source,
        "title": title,
        "text": body[:3000],
        "table": table,
        "note": note,
    }


# ---------------------------------------------------------------------------
# point d'entrée
# ---------------------------------------------------------------------------

def render(path: str, allow_office: bool = True, mode: str = "full") -> dict:
    """mode="fast" : jamais de COM, texte immédiat pour les fichiers Office.

    mode="full" : tentative de vraie page Office, repli texte.
    """
    started = time.perf_counter()
    try:
        st = os.stat(io_path(path))
    except OSError as exc:
        return {"ok": False, "error": f"Fichier introuvable ({exc.strerror})."}

    ext = _sniff_ext(io_path(path), Path(path).suffix.lower())
    want_image = ext == ".pdf" or ext in IMAGE_EXTS or (
        mode != "fast" and ext in OFFICE_EXTS
    )
    key = _cache_key(path, st)
    # Une image déjà en cache est toujours préférable au texte, même en mode rapide.
    cached = _load_cache(key, True) or _load_cache(key, want_image)
    if cached is not None:
        cached["cached"] = True
        cached["ms"] = round((time.perf_counter() - started) * 1000)
        cached.setdefault("path", path)
        cached.setdefault("name", Path(path).name)
        return cached

    result: dict
    if ext == ".pdf":
        try:
            result = _pdf_page_one(path)
        except Exception as exc:
            _report(path, exc)
            result = {"ok": False, "error": _friendly_error(exc)}
    elif ext in IMAGE_EXTS:
        try:
            result = _image_preview(path)
        except Exception as exc:
            _report(path, exc)
            result = {"ok": False, "error": _friendly_error(exc)}
    elif ext in OFFICE_EXTS:
        result = {"ok": False, "error": "Conversion Office impossible."}
        if mode != "fast" and allow_office and os.name == "nt":
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
            if mode == "full":
                result["note"] = result.get("note") or "Aperçu texte (Office indisponible)."
    else:
        result = _text_preview(ext, path)

    result["path"] = path
    result["name"] = Path(path).name
    result["ms"] = round((time.perf_counter() - started) * 1000)
    _store_cache(key, result)
    return result


def render_fast(path: str) -> dict:
    return render(path, allow_office=False, mode="fast")


def warm(path: str) -> None:
    """Préchauffe le cache sans bloquer l'appelant."""
    threading.Thread(target=render, args=(path,), daemon=True).start()
