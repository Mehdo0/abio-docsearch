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


def test_docx_tables_and_headings(tmp_path: Path) -> None:
    from docx import Document

    path = tmp_path / "proces_verbal.docx"
    doc = Document()
    doc.add_heading("Procès-verbal de recette", level=1)
    doc.add_paragraph("Matériel vérifié le 20 novembre.")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Point"
    table.cell(0, 1).text = "Résultat"
    table.cell(1, 0).text = "RFU"
    table.cell(1, 1).text = "Conforme"
    doc.save(path)

    result = preview.render(str(path), allow_office=False)
    assert result["ok"] is True
    assert "Procès-verbal" in result["title"]
    assert "Point" in result["text"] and "Conforme" in result["text"]
    assert "  |  " in result["text"]


def test_docx_without_styles_xml_still_previews(tmp_path: Path) -> None:
    """Certains DOCX produits par des outils tiers n'ont pas de styles.xml."""
    import shutil
    import zipfile

    from docx import Document

    source = tmp_path / "source.docx"
    doc = Document()
    doc.add_paragraph("Contenu récupérable malgré l'absence de styles.")
    doc.save(source)

    broken = tmp_path / "sans_styles.docx"
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(broken, "w") as dst:
        for item in src.infolist():
            if item.filename == "word/styles.xml":
                continue
            dst.writestr(item, src.read(item.filename))

    try:
        from docx import Document as Check

        Check(str(broken))
        pydocx_works = True
    except Exception:
        pydocx_works = False

    result = preview.render(str(broken), allow_office=False)
    assert result["ok"] is True
    assert "récupérable" in result["text"]
    assert result["source"] == "docx"


def test_docx_broken_container_is_readable(tmp_path: Path) -> None:
    """Archive sans [Content_Types].xml ni relations : le corps reste lisible."""
    import zipfile

    from docx import Document

    source = tmp_path / "base.docx"
    doc = Document()
    doc.add_paragraph("Le corps du document est intact.")
    doc.save(source)

    broken = tmp_path / "conteneur_casse.docx"
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(broken, "w") as dst:
        dst.writestr("word/document.xml", src.read("word/document.xml"))
        dst.writestr("docProps/core.xml", src.read("docProps/core.xml"))

    result = preview.render(str(broken), allow_office=False)
    assert result["ok"] is True
    assert "corps du document est intact" in result["text"]


def test_empty_docx_gives_a_clear_note(tmp_path: Path) -> None:
    import zipfile

    path = tmp_path / "vide.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("lisezmoi.txt", "pas un document word")

    result = preview.render(str(path), allow_office=False)
    assert result["ok"] is True
    assert result["note"]
    assert "KeyError" not in result["note"]


def test_csv_is_rendered_as_table(tmp_path: Path) -> None:
    path = tmp_path / "mesures.csv"
    path.write_text("Echantillon;RFU\nECH-01;1,42\nECH-02;1,38\n", encoding="utf-8")
    result = preview.render(str(path), allow_office=False)
    assert result["ok"] is True
    assert result["table"][0][0] == "Echantillon"
    assert result["table"][1][1] == "1,42"


def test_xlsx_skips_hidden_first_sheet(tmp_path: Path) -> None:
    from openpyxl import Workbook

    path = tmp_path / "classeur.xlsx"
    wb = Workbook()
    hidden = wb.active
    hidden.title = "Confidentiel"
    hidden.append(["Ne pas diffuser"])
    visible = wb.create_sheet("Public")
    visible.append(["Donnee", "Valeur"])
    visible.append(["RFU", 1.42])
    hidden.sheet_state = "hidden"
    wb.save(path)

    result = preview.render(str(path), allow_office=False)
    assert result["ok"] is True
    flat = " ".join(" ".join(row) for row in result["table"])
    assert "Donnee" in flat and "Ne pas diffuser" not in flat


def test_non_utf8_filename_does_not_raise(tmp_path: Path) -> None:
    import os

    name = os.fsdecode(b"note_invalide_\xff.txt")
    path = tmp_path / name
    path.write_bytes(b"Contenu lisible")
    result = preview.render(str(path), allow_office=False)
    assert result["ok"] is True
    assert "Contenu lisible" in result["text"]
