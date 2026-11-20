"""Ligne de commande : scan, recherche, statistiques.

L'interface graphique reste le chemin normal ; la CLI sert aux scripts et
aux vérifications rapides sur un poste sans session graphique.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .catalog import Catalog
from .paths import VERSION, db_path, human_size, load_config


def _progress_printer():
    last = [0.0]

    def printer(state: dict) -> None:
        now = time.perf_counter()
        if state.get("done"):
            return
        if now - last[0] < 0.25:
            return
        last[0] = now
        line = (
            f"\r  {state.get('files', 0):>9,} fichiers  "
            f"{state.get('dirs', 0):>7,} dossiers  "
            f"{state.get('elapsed', 0):>6.1f} s"
        ).replace(",", " ")
        current = state.get("current") or ""
        if current:
            room = max(10, 110 - len(line))
            if len(current) > room:
                current = "…" + current[-room:]
            line += "  " + current
        sys.stderr.write(line + "\x1b[K")
        sys.stderr.flush()

    return printer


def cmd_scan(args) -> int:
    cfg = load_config()
    roots = args.root or cfg.get("roots") or []
    if not roots:
        print("Aucune racine. Exemple :")
        print('  abio-docsearch-cli scan --root "T:\\_D2_Research_and_Development"')
        return 2
    missing = [r for r in roots if not Path(r).exists()]
    if len(missing) == len(roots):
        for r in missing:
            print(f"  ! introuvable : {r}")
        return 2
    if args.threads:
        cfg["threads"] = args.threads

    catalog = Catalog(Path(args.db) if args.db else db_path())
    print(f"Abionic DocSearch {VERSION} — {len(roots)} racine(s), "
          f"{cfg.get('threads', 8)} fils")
    result = catalog.scan(roots, cfg, None if args.quiet else _progress_printer())
    if not args.quiet:
        sys.stderr.write("\r\x1b[K")
    print()
    print(f"  fichiers   {result['files']:>9,}".replace(",", " "))
    print(f"  dossiers   {result['dirs']:>9,}".replace(",", " "))
    print(f"  ajoutés    {result['added']:>9,}".replace(",", " "))
    print(f"  disparus   {result['gone']:>9,}".replace(",", " "))
    print(f"  durée      {result['elapsed']:>9.1f} s  ({result['rate']:,} f/s)".replace(",", " "))
    print(f"  index      {human_size(result['db_size'])}")
    if result["errors"]:
        print(f"  erreurs    {result['errors']} dossiers illisibles")
    return 0


def cmd_search(args) -> int:
    catalog = Catalog(Path(args.db) if args.db else db_path())
    groups = [args.type] if args.type else []
    result = catalog.search(args.query, groups, args.limit, args.sort,
                            exts=[args.ext] if args.ext else None)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["files"] or result["folders"] else 1
    if not result["files"] and not result["folders"]:
        print("Aucun résultat.")
        return 1
    print(f"{len(result['files'])} fichier(s) — {result['ms']} ms")
    for item in result["files"]:
        print(f"  {item['size_human']:>9}  {item['date']}  {item['name']}")
        print(f"              {item['folder']}")
    if result["folders"]:
        print(f"\n{len(result['folders'])} dossier(s)")
        for item in result["folders"]:
            print(f"  {item['path']}")
    return 0


def cmd_stats(args) -> int:
    catalog = Catalog(Path(args.db) if args.db else db_path())
    stats = catalog.stats()
    print(f"  fichiers   {stats['files']:>9,}".replace(",", " "))
    print(f"  dossiers   {stats['dirs']:>9,}".replace(",", " "))
    print(f"  taille     {stats['size_human']:>9}")
    print(f"  dernier    {stats['last_scan'] or 'jamais'}")
    print(f"  racines    {', '.join(stats['roots']) or '—'}")
    print(f"  index      {human_size(stats['db_size'])}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="abio-docsearch-cli",
        description="Catalogue et recherche du partage Abionic.",
    )
    parser.add_argument("--db", help="chemin de l'index (défaut : données de l'app)")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="parcourir un ou plusieurs dossiers")
    scan.add_argument("--root", action="append", help="racine à parcourir (répétable)")
    scan.add_argument("--threads", type=int)
    scan.add_argument("--quiet", action="store_true")
    scan.set_defaults(func=cmd_scan)

    search = sub.add_parser("search", help="chercher un document")
    search.add_argument("query")
    search.add_argument("--ext", help="extension exacte, ex. .pdf")
    search.add_argument("--type", choices=["pdf", "word", "excel", "ppt", "image", "text"])
    search.add_argument("--limit", type=int, default=40)
    search.add_argument("--sort", choices=["relevance", "date", "name", "size"],
                        default="relevance")
    search.add_argument("--json", action="store_true")
    search.set_defaults(func=cmd_search)

    stats = sub.add_parser("stats", help="état de l'index")
    stats.set_defaults(func=cmd_stats)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
