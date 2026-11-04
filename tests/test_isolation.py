"""Separation runs in a child process so a native crash cannot close the window."""

import sys

import pytest

from stemn import gui


def _fake(monkeypatch, code: str):
    def cmd(path, model_id, result_file, progress_file, restore=False, bleed="off"):
        return [sys.executable, "-c", code, str(result_file), str(progress_file)]

    monkeypatch.setattr(gui, "separate_command", cmd)


def test_child_result_is_returned(monkeypatch, tmp_path):
    _fake(monkeypatch, "import json,sys; open(sys.argv[1],'w').write(json.dumps({'stems': {'x': {'kick': '/a/k.wav'}}}))")
    assert gui.run_separation(tmp_path / "x.wav", None) == {"kick": "/a/k.wav"}


def test_child_error_is_reported(monkeypatch, tmp_path):
    _fake(monkeypatch, "import json,sys; open(sys.argv[1],'w').write(json.dumps({'error': 'kaputt'})); sys.exit(3)")
    with pytest.raises(RuntimeError, match="kaputt"):
        gui.run_separation(tmp_path / "x.wav", None)


def test_child_crash_is_reported(monkeypatch, tmp_path):
    _fake(monkeypatch, "import os; os.abort()")
    with pytest.raises(RuntimeError, match="abgestuerzt"):
        gui.run_separation(tmp_path / "x.wav", None)


def test_progress_is_reported(monkeypatch, tmp_path):
    _fake(
        monkeypatch,
        "import json,sys,time\n"
        "for v in (0.1, 0.5, 1.0):\n"
        "    open(sys.argv[2],'w').write(str(v)); time.sleep(0.4)\n"
        "open(sys.argv[1],'w').write(json.dumps({'stems': {'x': {}}}))",
    )
    seen = []
    gui.run_separation(tmp_path / "x.wav", None, seen.append)
    assert seen and seen == sorted(seen) and seen[-1] == 1.0


def test_command_dev_mode(tmp_path):
    cmd = gui.separate_command(tmp_path / "a.wav", "m1", tmp_path / "r.json", tmp_path / "p.txt")
    assert cmd[1:4] == ["-m", "stemn.cli", "separate"]
    assert cmd[cmd.index("-q") + 1] == "best" and "--progress-file" in cmd
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


def test_restore_flag_reaches_child_command(tmp_path):
    cmd = gui.separate_command(tmp_path / "a.wav", None, tmp_path / "r.json", tmp_path / "p.txt", restore=True)
    assert cmd[-1] == "--restore"
    assert "--restore" not in gui.separate_command(tmp_path / "a.wav", None, tmp_path / "r.json", tmp_path / "p.txt")


def test_bleed_reaches_child_command(tmp_path):
    cmd = gui.separate_command(tmp_path / "a.wav", None, tmp_path / "r.json", tmp_path / "p.txt", bleed="strong")
    assert cmd[-2:] == ["--bleed", "strong"]
