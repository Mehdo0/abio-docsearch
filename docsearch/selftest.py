"""Auto-test embarqué : vérifie que l'exécutable peut réellement travailler.

Utilisé par la CI après le build Windows :

    AbionicDocSearch.exe --self-test
    abio-docsearch-cli.exe selftest

Le résultat est écrit dans un fichier pour les exécutables sans console.
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path


def run() -> int:
    root = Path(tempfile.mkdtemp(prefix="abiods-selftest-"))
    os.environ["DOCSEARCH_DATA"] = str(root / "data")
    lines: list[str] = [f"Abionic DocSearch — auto-test ({sys.platform})"]
    ok = True
    data_dir = None

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and condition
        lines.append(f"[{'OK ' if condition else 'ECHEC'}] {name}{' — ' + detail if detail else ''}")

    try:
        from .paths import VERSION, data_dir, ui_dir

        check("version", bool(VERSION), VERSION)
        check("interface embarquée", (ui_dir() / "index.html").exists(), str(ui_dir()))
        check("feuille de style", (ui_dir() / "styles.css").exists())
        check("police", (ui_dir() / "assets" / "sg-latin.woff2").exists())

        import fitz
        import docx
        import openpyxl
        import pptx

        check("PyMuPDF", bool(fitz.__doc__ or True))
        check("python-docx", bool(docx.__version__ if hasattr(docx, "__version__") else True))
        check("openpyxl", bool(openpyxl.__version__))
        check("python-pptx", bool(pptx.__version__))

        share = root / "share" / "Qualite"
        share.mkdir(parents=True)
        pdf_path = share / "CRS_abioCALIB_selftest.pdf"
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 90), "Auto-test DocSearch", fontsize=16)
        document.save(pdf_path)
        document.close()
        (share / "Note_interne.txt").write_text("note", encoding="utf-8")

        from .catalog import Catalog

        catalog = Catalog(data_dir() / "index.db")
        result = catalog.scan([str(root / "share")],
                              {"exclude_dirs": [], "threads": 2, "include_exts": []})
        check("scan", result["files"] == 2, f"{result['files']} fichiers")
        found = catalog.search("calib")
        check("recherche par préfixe", len(found["files"]) == 1, f"{len(found['files'])} résultat")

        from . import preview

        image = preview.render(str(pdf_path), allow_office=False)
        check("aperçu PDF", image.get("kind") == "image", f"{len(image.get('data') or '')} car.")
        cached = preview.render(str(pdf_path), allow_office=False)
        check("cache d'aperçu", cached.get("cached") is True)

        try:
            import webview  # noqa: F401

            check("pywebview", True)
        except ImportError as exc:
            if "--self-test" in sys.argv:
                raise
            check("pywebview (facultatif en CLI)", False, str(exc))
    except BaseException:
        ok = False
        lines.append("Exception :")
        lines.append(traceback.format_exc())

    lines.append("RESULTAT : " + ("OK" if ok else "ECHEC"))
    report = "\n".join(lines)
    try:
        log = (data_dir() if data_dir else root / "data") / "selftest.log"
        log.write_text(report + "\n", encoding="utf-8")
        print(report)
    except Exception:
        pass
    return 0 if ok else 1
