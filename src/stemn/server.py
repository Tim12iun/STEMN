"""Local HTTP service for Max for Live, Ableton Extensions and the app.

Only listens on 127.0.0.1. Keeps the model in memory between requests.

    GET  /health                -> {"ok": true, "version": "..."}
    GET  /models                -> [{"id": ..., "stems": [...]}, ...]
    POST /separate              <- {"input": "/abs/path.wav", "stems": ["kick"],
                                    "model": null, "output_dir": null,
                                    "quality": "normal", "restore": false,
                                    "bleed": "off"}
                                -> {"stems": {"kick": "/abs/path - kick.wav"}}
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__
from .models import ModelError, discover_models


def make_handler(engine):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"stemn/{__version__}"

        def log_message(self, fmt, *args):  # keep stdout clean for Max
            sys.stderr.write("stemn: " + (fmt % args) + "\n")

        def _send(self, status: int, payload) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                return self._send(200, {"ok": True, "version": __version__})
            if self.path == "/models":
                return self._send(200, [m.to_dict() for m in discover_models().values()])
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/separate":
                return self._send(404, {"error": "not found"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
                req = json.loads(self.rfile.read(length) or b"{}")
                if not req.get("input"):
                    return self._send(400, {"error": "'input' fehlt"})
                stems = req.get("stems")
                if isinstance(stems, str):
                    stems = [s for s in stems.split(",") if s]
                out = engine.separate(req["input"], stems=stems, model_id=req.get("model"), output_dir=req.get("output_dir"), quality=req.get("quality") or "best", restore=bool(req.get("restore")), bleed=req.get("bleed") or "off")
                return self._send(200, {"stems": {k: str(v) for k, v in out.items()}})
            except (ModelError, FileNotFoundError, ValueError) as exc:
                return self._send(400, {"error": str(exc)})
            except Exception as exc:  # pragma: no cover - surfaced to the caller
                return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    return Handler


def make_server(host: str = "127.0.0.1", port: int = 47800, engine=None) -> ThreadingHTTPServer:
    if engine is None:
        from .engine import default_engine

        engine = default_engine()
    return ThreadingHTTPServer((host, port), make_handler(engine))


def serve(host: str = "127.0.0.1", port: int = 47800) -> None:
    httpd = make_server(host, port)
    print(f"stemn laeuft auf http://{host}:{port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
