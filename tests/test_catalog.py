from __future__ import annotations

from pathlib import Path

from docsearch.catalog import Catalog


def make_share(root: Path) -> None:
    (root / "Qualite" / "CRS").mkdir(parents=True)
    (root / "Qualite" / "CRS" / "Rapport_définitif_abioCALIB.pdf").write_bytes(b"%PDF-1.4\n")
    (root / "Qualite" / "PRS").mkdir(parents=True)
    (root / "Qualite" / "PRS" / "Plan_de_validation.docx").write_bytes(b"PK\x03\x04")
    (root / "Archives" / "__Old").mkdir(parents=True)
    (root / "Archives" / "__Old" / "vieux_rapport.pdf").write_bytes(b"%PDF-1.4\n")
    (root / "Serveurs").mkdir()
    (root / "Serveurs" / "Inventaire_Linux.xlsx").write_bytes(b"PK\x03\x04")


CONFIG = {"exclude_dirs": ["__old"], "threads": 4, "include_exts": []}


def test_scan_counts_and_exclusions(tmp_path: Path) -> None:
    share = tmp_path / "share"
    make_share(share)
    catalog = Catalog(tmp_path / "index.db")
    result = catalog.scan([str(share)], CONFIG)

    assert result["files"] == 3
    assert result["gone"] == 0
    assert result["excluded"] >= 1
    stats = catalog.stats()
    assert stats["files"] == 3
    assert stats["last_scan"]
    assert stats["roots"] == [str(share)]


def test_search_is_accent_and_case_insensitive(tmp_path: Path) -> None:
    share = tmp_path / "share"
    make_share(share)
    catalog = Catalog(tmp_path / "index.db")
    catalog.scan([str(share)], CONFIG)

    result = catalog.search("DEFINITIF")
    assert [f["name"] for f in result["files"]] == ["Rapport_définitif_abioCALIB.pdf"]
    assert result["ms"] >= 0

    prefix = catalog.search("calib")
    assert prefix["files"], "la recherche par préfixe doit trouver abioCALIB"


def test_filters_sort_and_folders(tmp_path: Path) -> None:
    share = tmp_path / "share"
    make_share(share)
    catalog = Catalog(tmp_path / "index.db")
    catalog.scan([str(share)], CONFIG)

    pdfs = catalog.search("", groups=["pdf"])
    assert len(pdfs["files"]) == 1
    assert pdfs["files"][0]["ext"] == ".pdf"

    by_name = catalog.search("", sort="name")
    names = [f["name"] for f in by_name["files"]]
    assert names == sorted(names, key=str.lower)

    folders = catalog.search("Qualite")
    assert any(f["name"] == "Qualite" for f in folders["folders"])


def test_incremental_scan_and_deletion(tmp_path: Path) -> None:
    share = tmp_path / "share"
    make_share(share)
    catalog = Catalog(tmp_path / "index.db")
    catalog.scan([str(share)], CONFIG)

    second = catalog.scan([str(share)], CONFIG)
    assert second["added"] == 0
    assert second["files"] == 3

    (share / "Serveurs" / "Inventaire_Linux.xlsx").unlink()
    third = catalog.scan([str(share)], CONFIG)
    assert third["files"] == 2
    assert third["gone"] == 1
    assert catalog.search("Inventaire")["files"] == []


def test_cancelled_scan_keeps_index(tmp_path: Path) -> None:
    share = tmp_path / "share"
    make_share(share)
    catalog = Catalog(tmp_path / "index.db")
    catalog.scan([str(share)], CONFIG)

    import threading

    cancel = threading.Event()
    cancel.set()
    result = catalog.scan([str(share)], CONFIG, cancel=cancel)
    assert result["cancelled"] is True
    assert catalog.stats()["files"] == 3


def test_scan_ignores_archives_and_junk(tmp_path: Path) -> None:
    share = tmp_path / "share"
    share.mkdir()
    (share / "Rapport.pdf").write_bytes(b"%PDF-1.4\n")
    (share / "Sauvegarde.zip").write_bytes(b"PK\x03\x04")
    (share / "Installateur.exe").write_bytes(b"MZ")
    (share / "~$Rapport.docx").write_bytes(b"lock")
    (share / "Thumbs.db").write_bytes(b"db")
    (share / "note.tmp").write_bytes(b"tmp")

    catalog = Catalog(tmp_path / "index.db")
    config = {
        "exclude_dirs": [],
        "threads": 2,
        "include_exts": [],
        "exclude_exts": [".zip", ".exe"],
        "exclude_names": ["thumbs.db", "~$*", "*.tmp"],
    }
    result = catalog.scan([str(share)], config)

    assert result["files"] == 1
    assert result["ignored"] == 5
    assert [f["name"] for f in catalog.search("")["files"]] == ["Rapport.pdf"]


def test_default_config_excludes_archives(tmp_path: Path) -> None:
    from docsearch.paths import DEFAULT_CONFIG

    share = tmp_path / "share"
    share.mkdir()
    (share / "doc.pdf").write_bytes(b"%PDF-1.4\n")
    (share / "doc.zip").write_bytes(b"PK\x03\x04")
    (share / "doc.7z").write_bytes(b"7z")
    catalog = Catalog(tmp_path / "index.db")
    result = catalog.scan([str(share)], DEFAULT_CONFIG)
    assert result["files"] == 1
    assert result["ignored"] == 2
