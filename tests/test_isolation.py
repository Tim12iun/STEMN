"""Separation runs in a child process so a native crash cannot close the window."""

import sys

import pytest

from stemn import gui


def _fake(monkeypatch, code: str):
    def cmd(path, model_id, quality, result_file):
        return [sys.executable, "-c", code, str(result_file)]

    monkeypatch.setattr(gui, "separate_command", cmd)


def test_child_result_is_returned(monkeypatch, tmp_path):
    _fake(monkeypatch, "import json,sys; open(sys.argv[1],'w').write(json.dumps({'stems': {'x': {'kick': '/a/k.wav'}}}))")
    assert gui.run_separation(tmp_path / "x.wav", None, "fast") == {"kick": "/a/k.wav"}


def test_child_error_is_reported(monkeypatch, tmp_path):
    _fake(monkeypatch, "import json,sys; open(sys.argv[1],'w').write(json.dumps({'error': 'kaputt'})); sys.exit(3)")
    with pytest.raises(RuntimeError, match="kaputt"):
        gui.run_separation(tmp_path / "x.wav", None, "fast")


def test_child_crash_is_reported(monkeypatch, tmp_path):
    _fake(monkeypatch, "import os; os.abort()")
    with pytest.raises(RuntimeError, match="abgestuerzt"):
        gui.run_separation(tmp_path / "x.wav", None, "fast")


def test_command_dev_mode(tmp_path):
    cmd = gui.separate_command(tmp_path / "a.wav", "m1", "best", tmp_path / "r.json")
    assert cmd[1:4] == ["-m", "stemn.cli", "separate"]
    assert cmd[-2:] == ["-m", "m1"] and "--result-file" in cmd


def test_cli_writes_error_to_result_file(tmp_path):
    from stemn.cli import main

    result = tmp_path / "r.json"
    assert main(["separate", str(tmp_path / "fehlt.wav"), "--result-file", str(result)]) != 0
    assert "error" in result.read_text()


def test_separator_uses_cpu(monkeypatch):
    from stemn.backend import LocalSeparator

    monkeypatch.delenv("STEMN_DEVICE", raising=False)
    sep = LocalSeparator()
    assert sep.torch_device.type == "cpu"
    assert sep.onnx_execution_provider == ["CPUExecutionProvider"]
