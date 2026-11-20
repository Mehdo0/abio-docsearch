"""Point d'entrée de l'exécutable en ligne de commande (avec console)."""

from __future__ import annotations

import multiprocessing
import sys

from docsearch.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
