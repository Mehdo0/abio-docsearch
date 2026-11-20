"""Point d'entrée de l'exécutable graphique (aucune console)."""

from __future__ import annotations

import multiprocessing
import sys
import traceback
from pathlib import Path


def _crash(exc: BaseException) -> None:
    try:
        from docsearch.paths import data_dir

        log = Path(data_dir()) / "crash.log"
        log.write_text(
            "".join(traceback.format_exception(exc)), encoding="utf-8"
        )
        message = f"Abionic DocSearch n'a pas pu démarrer.\n\n{exc}\n\nJournal : {log}"
    except Exception:
        message = f"Abionic DocSearch n'a pas pu démarrer.\n\n{exc}"

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, "Abionic DocSearch", 0x10)
        except Exception:
            pass
    else:
        print(message, file=sys.stderr)


def run() -> int:
    if "--self-test" in sys.argv:
        from docsearch.selftest import run as selftest

        return selftest()

    from docsearch.app import main

    return main(debug="--debug" in sys.argv)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    try:
        raise SystemExit(run())
    except SystemExit:
        raise
    except BaseException as exc:  # l'exe fenêtré n'a pas de console
        _crash(exc)
        raise SystemExit(1)
