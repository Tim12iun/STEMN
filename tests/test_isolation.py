"""Separation runs in a child process so a native crash cannot close the window."""

import sys

import pytest

from stemn import gui


def _fake(monkeypatch, code: str):
    def cmd(path, model_id, result_file, progress_file, bleed="off"):
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
        "    open(sys.argv[2],'w').write(f'{v}\\nSpur {v}'); time.sleep(0.4)\n"
        "open(sys.argv[1],'w').write(json.dumps({'stems': {'x': {}}}))",
    )
    seen, notes = [], []
    gui.run_separation(tmp_path / "x.wav", None, lambda v, note: (seen.append(v), notes.append(note)))
    assert seen and seen == sorted(seen) and seen[-1] == 1.0
    assert notes[-1] == "Spur 1.0"


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


def test_bleed_reaches_child_command(tmp_path):
    cmd = gui.separate_command(tmp_path / "a.wav", None, tmp_path / "r.json", tmp_path / "p.txt", bleed="strong")
    assert cmd[-2:] == ["--bleed", "strong"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_child_killed_for_memory_gets_a_clear_message(monkeypatch, tmp_path):
    _fake(monkeypatch, "import os, signal; os.kill(os.getpid(), signal.SIGKILL)")
    with pytest.raises(RuntimeError, match="Arbeitsspeicher"):
        gui.run_separation(tmp_path / "x.wav", None)


def test_closing_the_window_ends_a_running_separation(monkeypatch, tmp_path):
    import threading
    import time

    _fake(monkeypatch, "import time; time.sleep(60)")
    errors = []

    def run():
        try:
            gui.run_separation(tmp_path / "x.wav", None)
        except RuntimeError as exc:
            errors.append(exc)

    t = threading.Thread(target=run)
    began = time.monotonic()
    t.start()
    while not gui._RUNNING and time.monotonic() - began < 10:
        time.sleep(0.05)
    gui.stop_separations()
    t.join(10)
    assert not t.is_alive() and errors and time.monotonic() - began < 15


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX parent handling")
def test_child_stops_when_the_app_is_gone(tmp_path):
    """If the window is force-quit, the separation process must not keep running."""
    import subprocess
    import time

    marker = tmp_path / "pid"
    # middle process starts a child that runs the watchdog, then exits at once
    child = (
        "import os,time,sys; sys.argv=['x']; from stemn.cli import _exit_with_parent; _exit_with_parent();"
        f"open({str(marker)!r},'w').write(str(os.getpid())); time.sleep(30)"
    )
    middle = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(1.5)"
    subprocess.run([sys.executable, "-c", middle], check=True)
    pid = int(marker.read_text())
    deadline = time.monotonic() + 10
    import os

    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.2)
    os.kill(pid, 9)
    raise AssertionError("separation process kept running after the app was gone")
