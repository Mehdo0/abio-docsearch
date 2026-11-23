"""Reprise d'une bibliothèque existante depuis l'interface."""

from __future__ import annotations

import pytest

from docsearch import paths as paths_mod
from docsearch import app as app_mod


def test_choose_data_dir_memorise_le_dossier(tmp_path, monkeypatch):
    courant = tmp_path / "courant"
    courant.mkdir()
    monkeypatch.setenv("DOCSEARCH_DATA", str(courant))

    api = app_mod.Api()
    monkeypatch.delenv("DOCSEARCH_DATA")
    monkeypatch.setattr(paths_mod, "pointer_file", lambda: tmp_path / "donnees.txt")

    ancienne = tmp_path / "tools" / "docsearch" / "data"
    ancienne.mkdir(parents=True)
    (ancienne / "index.db").write_bytes(b"")

    class Fenetre:
        def create_file_dialog(self, kind):
            return [str(ancienne.parent)]

    api._window = Fenetre()
    resultat = api.choose_data_dir()

    assert resultat["ok"], resultat
    assert resultat["data_dir"] == str(ancienne)
    assert paths_mod.data_dir() == ancienne
    assert (tmp_path / "donnees.txt").read_text(encoding="utf-8") == str(ancienne)


def test_choose_data_dir_refuse_un_dossier_sans_index(tmp_path, monkeypatch):
    courant = tmp_path / "courant"
    courant.mkdir()
    monkeypatch.setenv("DOCSEARCH_DATA", str(courant))
    api = app_mod.Api()
    monkeypatch.setattr(paths_mod, "pointer_file", lambda: tmp_path / "donnees.txt")

    vide = tmp_path / "vide"
    vide.mkdir()

    class Fenetre:
        def create_file_dialog(self, kind):
            return [str(vide)]

    api._window = Fenetre()
    resultat = api.choose_data_dir()

    assert not resultat["ok"]
    assert "index.db" in resultat["error"]
    assert not (tmp_path / "donnees.txt").exists()


def test_choose_data_dir_annulation(tmp_path, monkeypatch):
    courant = tmp_path / "courant"
    courant.mkdir()
    monkeypatch.setenv("DOCSEARCH_DATA", str(courant))
    api = app_mod.Api()

    class Fenetre:
        def create_file_dialog(self, kind):
            return None

    api._window = Fenetre()
    assert api.choose_data_dir() == {"ok": False, "cancelled": True}
