"""Harnais de développement : sert l'interface dans un navigateur headless.

Ce fichier ne part pas dans l'exécutable. Il permet de vérifier le rendu
et les appels API sans ouvrir de fenêtre native.

    DOCSEARCH_DATA=/tmp/abio-data python tools/dev_server.py 8899
"""

from __future__ import annotations

import json
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from docsearch.app import Api  # noqa: E402
from docsearch.paths import ui_dir  # noqa: E402


class Handler(SimpleHTTPRequestHandler):
    api = Api()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ui_dir()), **kwargs)

    def log_message(self, fmt, *args):  # silence
        pass

    def do_POST(self):
        if not self.path.startswith("/api/"):
            self.send_error(404)
            return
        name = self.path[5:]
        length = int(self.headers.get("content-length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        args = payload.get("args") or []
        if name in ("choose_folders", "attach"):
            result = self.api.config.get("roots", []) if name == "choose_folders" else None
        else:
            fn = getattr(self.api, name, None)
            result = fn(*args) if fn else {"error": f"méthode inconnue : {name}"}
        body = json.dumps(result).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    print(f"http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
