from __future__ import annotations

from pathlib import Path

import pytest

from docsearch import preview


@pytest.fixture(autouse=True)
def isolated_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCSEARCH_DATA", str(tmp_path / "data"))


def _pdf(path: Path, text: str = "Rapport de validation") -> None:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 90), text, fontsize=16)
    doc.save(path)
    doc.close()


def test_pdf_preview_is_real_page(tmp_path: Path) -> None:
    path = tmp_path / "rapport.pdf"
    _pdf(path)

    result = preview.render(str(path), allow_office=False)
    assert result["ok"] is True
    assert result["kind"] == "image"
    assert result["source"] == "pdf"
    assert result["width"] == 900
    assert len(result["data"]) > 1000

    cached = preview.render(str(path), allow_office=False)
    assert cached["cached"] is True
    assert cached["data"] == result["data"]


def test_docx_falls_back_to_text_page(tmp_path: Path) -> None:
    from docx import Document

    path = tmp_path / "procedure.docx"
    doc = Document()
    doc.add_heading("Procédure de vérification", level=0)
    doc.add_paragraph("Les critères d'acceptation sont définis ci-dessous.")
    doc.save(path)

    result = preview.render(str(path), allow_office=False)
    assert result["ok"] is True
    assert result["kind"] == "text"
    assert "Procédure de vérification" in result["title"]
    assert "critères" in result["text"]


def test_xlsx_preview_has_table(tmp_path: Path) -> None:
    from openpyxl import Workbook

    path = tmp_path / "mesures.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["Echantillon", "RFU"])
    ws.append(["ECH-01", 1.42])
    wb.save(path)

    result = preview.render(str(path), allow_office=False)
    assert result["ok"] is True
    assert result["table"][0][0] == "Echantillon"
    assert result["table"][1][1] == "1.42"


def test_text_preview_and_errors(tmp_path: Path) -> None:
    path = tmp_path / "note.txt"
    path.write_text("Note interne\nDeuxième ligne", encoding="utf-8")
    result = preview.render(str(path), allow_office=False)
    assert result["kind"] == "text"
    assert result["text"].startswith("Note interne")

    assert preview.render(str(tmp_path / "absent.pdf"))["ok"] is False

    broken = tmp_path / "vide.pdf"
    broken.write_bytes(b"")
    assert preview.render(str(broken), allow_office=False)["ok"] is False


def test_cache_invalidated_when_file_changes(tmp_path: Path) -> None:
    path = tmp_path / "rapport.pdf"
    _pdf(path, "Version un")
    first = preview.render(str(path), allow_office=False)
    assert first.get("cached") is not True

    import os
    import time

    time.sleep(0.01)
    _pdf(path, "Version deux, plus longue")
    os.utime(path, (time.time() + 5, time.time() + 5))
    second = preview.render(str(path), allow_office=False)
    assert second.get("cached") is not True
