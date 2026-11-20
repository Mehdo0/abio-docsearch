"""Génère un petit partage de démonstration (documents réels + noms variés).

    python tools/make_corpus.py /tmp/abio-share --count 400
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

WORDS = [
    "abioCALIB", "abioSCOPE", "PSP", "CRS", "PRS", "Design", "Change", "Review",
    "Validation", "Verification", "Protocole", "Recette", "Maintenance", "Serveur",
    "Linux", "Upgrade", "Plan", "Rapport", "Audit", "Qualite", "Regression",
    "abioBANK", "abioGEN", "abioDEPLOY", "Cartographie", "Risque", "Fournisseur",
]
EXTS = ["pdf", "pdf", "pdf", "docx", "docx", "xlsx", "pptx", "png", "txt"]
FOLDERS = [
    "01_Qualite/CRS", "01_Qualite/PRS", "02_Software/abioCALIB",
    "02_Software/abioBANK", "03_Serveurs/Linux", "04_Projets/abioGEN",
    "05_Archives/__Old", "06_Recettes/2026",
]


def _pdf(path: Path, title: str, pages: int = 3) -> None:
    import pymupdf

    doc = pymupdf.open()
    for page_no in range(pages):
        page = doc.new_page()
        page.insert_text((72, 90), title, fontsize=18, fontname="helv")
        page.insert_text(
            (72, 130),
            f"Document de demonstration - page {page_no + 1}\n"
            "Abionic SA - service Qualite et Affaires Reglementaires\n\n"
            "Ce document decrit la procedure de verification logicielle,\n"
            "les criteres d'acceptation et les preuves associees.\n\n"
            "Reference interne : DOC-" + path.stem[-6:],
            fontsize=10,
            fontname="helv",
        )
        page.draw_rect(pymupdf.Rect(72, 260, 520, 262), color=(0.03, 0.68, 0.71), fill=(0.03, 0.68, 0.71))
    doc.save(path)
    doc.close()


def _docx(path: Path, title: str) -> None:
    from docx import Document

    doc = Document()
    doc.add_heading(title, level=0)
    doc.add_paragraph("Abionic SA — document de démonstration.")
    doc.add_heading("1. Objet", level=1)
    doc.add_paragraph(
        "Ce document définit la méthode de vérification et les critères "
        "d'acceptation applicables au logiciel concerné."
    )
    doc.add_heading("2. Périmètre", level=1)
    doc.add_paragraph(
        "Les exigences couvertes sont celles du cycle de vie logiciel : "
        "conception, implémentation, tests unitaires et tests de régression."
    )
    table = doc.add_table(rows=4, cols=3)
    table.style = "Table Grid"
    for i, row in enumerate([
        ("Exigence", "Critère", "Statut"),
        ("EX-01", "Traçabilité complète", "Conforme"),
        ("EX-02", "Journal des modifications", "Conforme"),
        ("EX-03", "Revue indépendante", "En cours"),
    ]):
        for j, value in enumerate(row):
            table.cell(i, j).text = value
    doc.save(path)


def _xlsx(path: Path) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Résultats"
    ws.append(["Identifiant", "Mesure", "RFU", "Statut"])
    for i in range(1, 15):
        ws.append([f"ECH-{i:03}", round(random.uniform(0.2, 3.4), 2),
                   round(random.uniform(0.5, 2.8), 2), "OK" if i % 4 else "À revoir"])
    wb.save(path)


def _pptx(path: Path, title: str) -> None:
    from pptx import Presentation

    pres = Presentation()
    slide = pres.slides.add_slide(pres.slide_layouts[0])
    slide.shapes.title.text = title
    slide.placeholders[1].text = "Revue de direction — Abionic SA"
    second = pres.slides.add_slide(pres.slide_layouts[1])
    second.shapes.title.text = "Points clés"
    second.placeholders[1].text = "Traçabilité\nRevue des risques\nPlan d'action"
    pres.save(path)


def _png(path: Path, title: str) -> None:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((60, 100), title, fontsize=20, fontname="helv")
    page.draw_circle((300, 400), 120, color=(0.03, 0.49, 0.53), fill=(0.86, 0.96, 0.95))
    pix = page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5))
    pix.save(path)
    doc.close()


def build(root: Path, count: int) -> None:
    random.seed(7)
    root.mkdir(parents=True, exist_ok=True)
    for folder in FOLDERS:
        (root / folder).mkdir(parents=True, exist_ok=True)

    for i in range(count):
        folder = root / random.choice(FOLDERS)
        title = " ".join(random.sample(WORDS, 3))
        ext = random.choices(EXTS, weights=[30, 30, 30, 18, 18, 8, 6, 4, 6])[0] if i < len(EXTS) else random.choice(EXTS)
        name = f"D{i:04}_{title.replace(' ', '_')}.{ext}"
        path = folder / name
        try:
            if ext == "pdf":
                _pdf(path, title)
            elif ext == "docx":
                _docx(path, title)
            elif ext == "xlsx":
                _xlsx(path)
            elif ext == "pptx":
                _pptx(path, title)
            elif ext == "png":
                _png(path, title)
            else:
                path.write_text(f"{title}\n\nDocument texte de demonstration.\n", encoding="utf-8")
        except Exception as exc:  # pragma: no cover
            print(f"  ! {name}: {exc}")

    # volume de noms pour le banc d'essai
    big = root / "07_Volume"
    big.mkdir(exist_ok=True)
    for i in range(count * 4):
        sub = big / f"Lot_{i // 200:02d}"
        sub.mkdir(exist_ok=True)
        (sub / f"Note_{i:05d}_{random.choice(WORDS)}.pdf").touch()

    old = root / "05_Archives" / "__Old"
    (old / "vieux_document.pdf").touch()
    print(f"Corpus prêt : {root}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default="/tmp/abio-share")
    parser.add_argument("--count", type=int, default=400)
    args = parser.parse_args()
    build(Path(args.root), args.count)
