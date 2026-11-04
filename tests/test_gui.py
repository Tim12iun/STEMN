"""Window logic without a display (Qt offscreen)."""

import os

import numpy as np
import pytest
import soundfile as sf

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtWidgets import QApplication  # noqa: E402

from stemn import gui  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    sr = 44100
    stems = {}
    for i, name in enumerate(["kick", "snare", "hihat"]):
        path = tmp_path / f"loop - {name}.wav"
        sf.write(path, np.full((sr, 2), 0.1 * (i + 1), dtype=np.float32), sr)
        stems[name] = path
    w = gui.MainWindow()
    w.source = tmp_path / "loop.wav"
    w.on_done({k: str(v) for k, v in stems.items()})
    yield w
    # destroy Qt objects deterministically; letting Python's GC do it later segfaults
    import gc

    from PySide6.QtCore import QCoreApplication, QEvent

    w.close()
    w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    gc.collect()


def test_rows_in_drum_order(window):
    assert [r.stem for r in window.rows] == ["kick", "snare", "hihat"]


def test_tick_boxes_and_only_this(window):
    assert window.audible() == ["kick", "snare", "hihat"]  # all ticked after separating
    window.rows[1].check.setChecked(False)
    assert window.audible() == ["kick", "hihat"]
    window.only_this("hihat")  # right click / Alt-click / click on the name
    assert window.audible() == ["hihat"]
    window.only_this("hihat")  # again: all back on
    assert window.audible() == ["kick", "snare", "hihat"]


def test_singles_drag_gives_one_file_per_ticked_stem(window):
    window.rows[1].check.setChecked(False)
    window.rows[2].fader.setValue(-30)
    files = window.export_singles()
    assert files[0] == window.stems["kick"]
    assert files[1].name == "loop - hihat -3.0dB.wav" and files[1] != window.stems["hihat"]
    assert len(files) == 2


def test_player_mixes_audible_stems(window):
    window.rows[0].check.setChecked(False)  # kick off -> snare 0.2 + hihat 0.3
    out = np.zeros((256, 2), dtype=np.float32)
    window.player._callback(out, 256, None, None)
    assert np.allclose(out, 0.5, atol=1e-3)


def test_export_mix_writes_summed_file(window):
    window.rows[0].check.setChecked(False)
    path = window.export_mix()
    audio, _ = sf.read(str(path))
    assert path.name == "loop - mix snare+hihat.wav"
    assert np.allclose(audio, 0.5, atol=1e-3)


def test_single_stem_drag_uses_stem_file(window):
    window.only_this("kick")
    assert window.export_mix() == window.stems["kick"]


def test_replacing_loop_rebuilds_rows(window, app):
    import gc

    stems = {k: str(v) for k, v in window.stems.items()}
    for _ in range(3):
        window.clear_rows()
        app.processEvents()
        gc.collect()
        window.on_done(stems)
    assert len(window.rows) == 3


def test_gain_fader_scales_playback_and_mix(window):
    window.rows[1].check.setChecked(False)
    window.rows[2].check.setChecked(False)
    window.rows[0].fader.setValue(-60)  # kick -6 dB
    assert window.rows[0].db.text() == "-6.0 dB"
    out = np.zeros((64, 2), dtype=np.float32)
    window.player._callback(out, 64, None, None)
    assert np.allclose(out, 0.1 * 10 ** (-6 / 20), atol=1e-3)
    path = window.export_mix()  # single stem but not at 0 dB -> rendered file
    assert path != window.stems["kick"]
    assert path.name == "loop - mix kick-6.0dB.wav"
    kick, _ = sf.read(str(window.stems["kick"]))
    assert np.allclose(kick, 0.1, atol=1e-3)  # original stem untouched


def test_fader_minimum_is_silent(window):
    window.rows[0].fader.setValue(window.rows[0].fader.minimum())
    assert window.current_gains()["kick"] == 0.0
    assert "kick" not in window.wave.active


def test_every_stem_row_has_its_own_grip(window):
    assert window.rows
    for strip in window.rows:
        assert strip.grip._path_provider() == strip.path


class _FakeStream:
    latency = 0.01

    def stop(self):
        pass

    def close(self):
        pass


def test_space_plays_from_click_and_pause_keeps_position(window):
    player = window.player
    player.stream, player._stream_sr = _FakeStream(), player.sr  # no audio device in tests
    window.on_seek(0.5)
    start = player.pos
    assert start == int(0.5 * player.length)
    window.toggle_play()  # Space
    assert player.playing
    out = np.zeros((4410, 2), np.float32)
    player._callback(out, 4410, None, None)  # 0.1 s played
    window.toggle_play()  # Space again: stop
    assert not player.playing
    stopped = player.pos
    assert 0 < stopped - start <= 4410
    player._callback(out, 4410, None, None)  # device keeps running, outputs silence
    assert not out.any() and player.pos == stopped
    window.toggle_play()
    assert player.playing and player.pos == stopped
    player.close()
