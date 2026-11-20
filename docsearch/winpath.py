"""Chemins Windows étendus.

Windows limite la plupart des appels fichiers à 260 caractères. Le préfixe
``\\\\?\\`` lève cette limite sans configuration ni droits administrateur.
Les chemins affichés et stockés restent normaux ; seuls les appels d'entrée/sortie
utilisent la forme étendue.
"""

from __future__ import annotations

import os
from pathlib import Path

EXT_PREFIX = "\\\\?\\"
UNC_PREFIX = "\\\\?\\UNC\\"


def extended(path: str, *, windows: bool = True) -> str:
    """Forme étendue d'un chemin (fonction pure, testable hors Windows)."""
    text = str(path)
    if not windows or text.startswith(EXT_PREFIX):
        return text
    text = text.replace("/", "\\")
    if text.startswith("\\\\"):
        # \\serveur\partage\... -> \\?\UNC\serveur\partage\...
        return UNC_PREFIX + text[2:]
    # Chemin déjà absolu avec lecteur : ne pas dépendre du système courant
    # (utile aussi pour tester la logique hors Windows).
    absolute = text if len(text) > 2 and text[1] == ":" and text[2] == "\\" else os.path.abspath(text)
    if len(absolute) < 240:
        return absolute
    return EXT_PREFIX + absolute


def io_path(path: str | Path) -> str:
    """Renvoie un chemin utilisable par les appels système, préfixé si nécessaire."""
    return extended(str(path), windows=(os.name == "nt"))


def display_path(path: str | Path) -> str:
    """Retire le préfixe étendu pour l'affichage, le stockage et le presse-papiers."""
    text = str(path)
    if text.startswith(UNC_PREFIX):
        return "\\\\" + text[len(UNC_PREFIX):]
    if text.startswith(EXT_PREFIX):
        return text[len(EXT_PREFIX):]
    return text
