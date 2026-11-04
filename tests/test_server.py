import json
import threading
import urllib.request
from pathlib import Path

import pytest

from stemn.server import make_server


class FakeEngine:
    def __init__(self):
        self.calls = []

    def separate(self, input_path, stems=None, model_id=None, output_dir=None, quality="normal"):
        self.calls.append((input_path, stems, model_id, output_dir))
        if not Path(input_path).exists():
            raise FileNotFoundError(input_path)
        return {s: Path(f"/tmp/{s}.wav") for s in (stems or ["kick", "snare"])}


@pytest.fixture
def server():
    engine = FakeEngine()
    httpd = make_server("127.0.0.1", 0, engine=engine)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", engine
    httpd.shutdown()
    httpd.server_close()


def _post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def test_health(server):
    url, _ = server
    with urllib.request.urlopen(url + "/health") as r:
        assert json.load(r)["ok"] is True


def test_separate_kick(server, tmp_path):
    url, engine = server
    f = tmp_path / "loop.wav"
    f.write_bytes(b"x")
    status, body = _post(url + "/separate", {"input": str(f), "stems": "kick"})
    assert status == 200
    assert list(body["stems"]) == ["kick"]
    assert engine.calls[0][1] == ["kick"]


def test_separate_errors(server):
    url, _ = server
    assert _post(url + "/separate", {})[0] == 400
    assert _post(url + "/separate", {"input": "/nope.wav"})[0] == 400
