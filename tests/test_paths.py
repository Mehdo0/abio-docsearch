"""Choix du dossier de données : un exécutable déplacé ne doit rien masquer."""

from __future__ import annotations

import json
import sys

import pytest

from docsearch import paths


@pytest.fixture()
def deplace(tmp_path, monkeypatch):
    """Simule un exécutable gelé, déplacé dans un dossier inscriptible."""
    exe_dir = tmp_path / "bureau"
    exe_dir.mkdir()
    exe = exe_dir / "AbionicDocSearch.exe"
    exe.write_bytes(b"MZ")
    legacy = tmp_path / "localappdata" / "Abionic" / "DocSearch"

    monkeypatch.delenv("DOCSEARCH_DATA", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe), raising=False)
    monkeypatch.setattr(paths, "_legacy_dir", lambda: legacy)
    return {"portable": exe_dir / "data", "legacy": legacy}


def _bibliotheque(dossier, taille, roots):
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "index.db").write_bytes(b"\0" * taille)
    (dossier / "config.json").write_text(
        json.dumps({"roots": roots}), encoding="utf-8"
    )


def test_exe_deplace_retrouve_la_bibliotheque(deplace):
    _bibliotheque(deplace["legacy"], 4096, ["\\\\serveur\\documents"])
    # Nouvel emplacement : dossier data/ créé vide par le lancement.
    deplace["portable"].mkdir(parents=True, exist_ok=True)

    assert paths.data_dir() == deplace["legacy"]


def test_index_vide_sans_dossier_analyse_ne_masque_pas(deplace):
    _bibliotheque(deplace["legacy"], 4096, ["D:\\docs"])
    _bibliotheque(deplace["portable"], 0, [])

    assert paths.data_dir() == deplace["legacy"]


def test_dossier_portable_avec_bibliotheque_gagne(deplace):
    _bibliotheque(deplace["legacy"], 1024, [])
    _bibliotheque(deplace["portable"], 8192, ["E:\\partage"])

    assert paths.data_dir() == deplace["portable"]


def test_installation_neuve_reste_portable(deplace):
    assert paths.data_dir() == deplace["portable"]
    assert deplace["portable"].is_dir()


def test_variable_d_environnement_prioritaire(deplace, tmp_path, monkeypatch):
    _bibliotheque(deplace["legacy"], 4096, ["D:\\docs"])
    cible = tmp_path / "ailleurs"
    monkeypatch.setenv("DOCSEARCH_DATA", str(cible))

    assert paths.data_dir() == cible.resolve()


def test_sans_executable_gele_emplacement_historique(monkeypatch, tmp_path):
    legacy = tmp_path / "historique"
    monkeypatch.delenv("DOCSEARCH_DATA", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(paths, "_legacy_dir", lambda: legacy)

    assert paths.data_dir() == legacy

def test_dossier_choisi_est_prioritaire(deplace):
    _bibliotheque(deplace["legacy"], 4096, ["D:\\docs"])
    choisi = deplace["portable"].parent / "ailleurs"
    _bibliotheque(choisi, 2048, ["E:\\partage"])

    paths.set_data_dir_pointer(choisi)

    assert paths.data_dir() == choisi
    assert paths.pointer_file().name == "donnees.txt"
    assert paths.pointer_file().read_text(encoding="utf-8") == str(choisi)


def test_pointeur_obsolete_est_ignore(deplace, tmp_path):
    _bibliotheque(deplace["legacy"], 4096, ["D:\\docs"])
    paths.set_data_dir_pointer(tmp_path / "disque-absent")

    assert paths.data_dir() == deplace["legacy"]


def test_library_root_accepte_le_dossier_ou_son_data(tmp_path):
    from docsearch.app import _library_root

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "index.db").write_bytes(b"x")
    (tmp_path / "vide").mkdir()

    assert _library_root(tmp_path) == tmp_path / "data"
    assert _library_root(tmp_path / "data") == tmp_path / "data"
    assert _library_root(tmp_path / "vide") is None


def test_pointeur_sans_index_n_ecrase_pas_la_bibliotheque(deplace, tmp_path):
    """Le bug : donnees.txt vers un dossier vide masquait l'index réel."""
    _bibliotheque(deplace["portable"], 8192, ["E:\\partage"])
    vide = tmp_path / "dossier-sans-index"
    vide.mkdir()
    paths.set_data_dir_pointer(vide)

    assert paths.data_dir() == deplace["portable"]

    info = paths.data_info()
    assert info["path"] == str(deplace["portable"])
    assert info["has_index"] is True
    assert any("index.db" in w for w in info["warnings"])
    assert info["source"] == paths.SOURCE_APP


def test_data_info_designe_le_dossier_et_son_origine(deplace):
    _bibliotheque(deplace["portable"], 4096, ["E:\\partage"])

    info = paths.data_info()

    assert info["path"] == str(deplace["portable"])
    assert info["source"] == paths.SOURCE_APP
    assert info["writable"] is True
    assert info["index_bytes"] > 0
    labels = [c["label"] for c in info["candidates"]]
    assert "Dossier de l'application" in labels


def test_pointeur_vers_le_dossier_parent_est_corrige(deplace):
    """donnees.txt = dossier de l'exe : utiliser son sous-dossier data."""
    _bibliotheque(deplace["portable"], 4096, ["E:\\partage"])
    paths.set_data_dir_pointer(deplace["portable"].parent)

    assert paths.data_dir() == deplace["portable"]
    assert paths.pointer_file().read_text(encoding="utf-8") == str(deplace["portable"])
    assert paths.data_info()["source"] == paths.SOURCE_POINTER
