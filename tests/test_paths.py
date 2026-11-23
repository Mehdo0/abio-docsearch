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
