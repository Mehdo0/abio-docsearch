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


def test_scan_reads_paths_beyond_260_characters(tmp_path: Path) -> None:
    """Windows masque les chemins > 260 caractères sans le préfixe étendu."""
    share = tmp_path / "share"
    deep = share
    for index in range(12):
        deep = deep / (f"dossier_{index:02d}_" + "x" * 25)
    deep.mkdir(parents=True)
    document = deep / "Rapport_tres_profond.txt"
    document.write_text("contenu profond", encoding="utf-8")
    assert len(str(document)) > 300

    catalog = Catalog(tmp_path / "index.db")
    result = catalog.scan([str(share)], CONFIG)

    assert result["files"] == 1
    found = catalog.search("profond")["files"]
    assert [f["name"] for f in found] == ["Rapport_tres_profond.txt"]
    assert found[0]["path"] == str(document)
    assert not found[0]["path"].startswith("\\\\?\\")


def test_scan_inaccessible_ne_vide_pas_l_index(tmp_path: Path) -> None:
    """Partage déconnecté : on garde l'index au lieu de le purger."""
    share = tmp_path / "share"
    make_share(share)
    catalog = Catalog(tmp_path / "index.db")
    premier = catalog.scan([str(share)], CONFIG)
    assert premier["files"] == 3

    share.rename(tmp_path / "share-hors-ligne")
    second = catalog.scan([str(share)], CONFIG)

    assert second["files"] == 3
    assert second["purged"] is False
    assert second["failed_roots"] == [str(share)]
    assert catalog.search("definitif")["files"], "les documents doivent rester cherchables"


def test_reglages_en_chaines_n_ignorent_plus_tout(tmp_path: Path) -> None:
    """Ancien config.json : "include_exts": ".pdf,.docx" (chaîne, pas liste)."""
    share = tmp_path / "share"
    make_share(share)
    catalog = Catalog(tmp_path / "index.db")
    result = catalog.scan([str(share)], {
        "threads": 4,
        "exclude_dirs": "__old",
        "include_exts": ".pdf,.docx",
        "exclude_exts": ".zip",
        "exclude_names": "thumbs.db,desktop.ini",
    })

    assert result["files"] == 2, result
    assert result["ignored"] >= 1


def test_total_et_offset_pour_afficher_les_suivants(tmp_path: Path) -> None:
    share = tmp_path / "share"
    (share / "Qualite" / "CRS").mkdir(parents=True)
    for i in range(25):
        (share / "Qualite" / "CRS" / f"Rapport_qualite_{i:02d}.pdf").write_bytes(b"%PDF")
    (share / "Qualite" / "Autre").mkdir()
    (share / "Qualite" / "Autre" / "Note.pdf").write_bytes(b"%PDF")

    catalog = Catalog(tmp_path / "index.db")
    catalog.scan([str(share)], CONFIG)

    first = catalog.search("rapport", limit=10)
    assert first["total"] == 25
    assert len(first["files"]) == 10

    second = catalog.search("rapport", limit=10, offset=10)
    assert second["total"] == 25
    assert len(second["files"]) == 10
    assert not ({f["path"] for f in first["files"]} & {f["path"] for f in second["files"]})

    last = catalog.search("rapport", limit=10, offset=20)
    assert len(last["files"]) == 5


def test_recherche_limitee_a_un_dossier(tmp_path: Path) -> None:
    share = tmp_path / "share"
    make_share(share)
    catalog = Catalog(tmp_path / "index.db")
    catalog.scan([str(share)], CONFIG)

    dossier = str(share / "Qualite" / "CRS")
    result = catalog.search("", folder=dossier)
    assert result["total"] == 1
    assert [f["name"] for f in result["files"]] == ["Rapport_définitif_abioCALIB.pdf"]
    assert result["folder"] == dossier

    parent = str(share / "Qualite")
    result = catalog.search("", folder=parent)
    assert result["total"] == 2
