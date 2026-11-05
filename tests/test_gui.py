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


def test_names_switch_stems_and_solo_button(window):
    assert window.audible() == ["kick", "snare", "hihat"]  # all on after separating
    window.toggle_stem("snare")  # click on the name
    assert window.audible() == ["kick", "hihat"]
    window.toggle_solo("hihat")  # S lights up, only hi-hat
    assert window.audible() == ["hihat"]
    assert [r.solo.isChecked() for r in window.rows] == [False, False, True]
    window.toggle_solo("hihat")  # S again: back to before
    assert window.audible() == ["kick", "hihat"]
    assert not any(r.solo.isChecked() for r in window.rows)


def test_clicking_a_name_ends_solo(window):
    window.toggle_solo("kick")
    window.toggle_stem("snare")  # another stem comes in, S goes dark
    assert window.audible() == ["kick", "snare"]
    assert not any(r.solo.isChecked() for r in window.rows)


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
    window.toggle_solo("kick")
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


def test_gear_shows_dot_when_update_is_waiting(window):
    assert not window.gear_btn.badge
    window._installing = False
    window._on_update_checked("9.9.9", "")
    assert window.gear_btn.badge and "9.9.9" in window.gear_btn.toolTip()
    dlg = gui.SettingsDialog(window)
    assert dlg.install_btn.isVisibleTo(dlg) and "9.9.9" in dlg.status.text()
    window._on_update_installed("9.9.9", "")
    assert dlg.install_btn.text() == "NEU STARTEN"
    window._on_update_checked("", "")
    assert not window.gear_btn.badge
    window.update_changed.disconnect(dlg.refresh)
    dlg.deleteLater()


def test_separate_stays_visible_and_glows_only_when_needed(window):
    assert window.separate_btn.isVisibleTo(window) and window.export_btn.isVisibleTo(window)
    assert window.separate_btn.property("lit") is False and window.export_btn.property("lit") is True
    window.model_box.addItem("Anderes Modell", "other-model")
    i = window.model_box.currentIndex()
    window.model_box.setCurrentIndex(window.model_box.count() - 1)  # another model needs a new separation
    assert window.separate_btn.property("lit") is True and window.export_btn.property("lit") is False
    window.model_box.setCurrentIndex(i)  # back to what the stems were made with
    assert window.separate_btn.property("lit") is False


def test_master_volume_scales_playback_only(window):
    import numpy as np

    out = np.zeros((256, 2), np.float32)
    window.player._callback(out, 256, None, None)
    loud = np.abs(out).max()
    window.volume.setValue(-20)
    window.player._callback(out, 256, None, None)
    assert np.abs(out).max() == pytest.approx(loud * 0.1, rel=1e-3)
    assert all(g == 1.0 for g in window.player.gains.values())  # export levels untouched
    window.volume.setValue(-40)
    window.player._callback(out, 256, None, None)
    assert not out.any()


def test_nothing_moves_when_clicking(window, app):
    """loomn: no click changes the size or place of anything on screen."""
    from PySide6.QtWidgets import QWidget

    window.resize(1300, 640)
    window.show()
    app.processEvents()

    def snapshot():
        app.processEvents()
        return {id(w): (w.objectName(), w.geometry().getRect()) for w in window.findChildren(QWidget) if w.isVisible()}

    before = snapshot()
    actions = [
        lambda: (window.play_btn.setChecked(True), window.play_btn.setText("❚❚")),
        lambda: (window.play_btn.setChecked(False), window.play_btn.setText("▶")),
        lambda: window.toggle_solo("kick"),
        lambda: window.toggle_solo("kick"),
        lambda: window.toggle_stem("snare"),
    ]
    for act in actions:
        act()
        after = snapshot()
        moved = [before[k] for k in before if k in after and after[k][1] != before[k][1]]
        assert not moved, moved


def test_cancel_asks_first_and_keeps_the_previous_stems(window, monkeypatch):
    stems_before = dict(window.stems)
    stopped = []
    monkeypatch.setattr(gui, "stop_separations", lambda: stopped.append(True))
    window._thread = object()  # a run is going
    monkeypatch.setattr(window, "_confirm_cancel", lambda: False)
    window.cancel_separation()
    assert not stopped  # said "keep rendering"
    monkeypatch.setattr(window, "_confirm_cancel", lambda: True)
    window.cancel_separation()
    assert stopped
    window.on_failed("killed")
    window._thread = None
    assert window.stems == stems_before and "vorherige Trennung" in window.status.text()


def test_x_in_progress_box_requests_cancel(window, app):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    asked = []
    window.wave.cancel_requested.connect(lambda: asked.append(True))
    window.resize(1300, 560)
    window.show()
    window.wave.set_progress(0.4, "Trenne Spuren")
    window.wave.repaint()
    app.processEvents()
    r = window.wave._cancel_rect
    QTest.mouseClick(window.wave, Qt.LeftButton, pos=QPoint(int(r.center().x()), int(r.center().y())))
    assert asked


def test_stale_work_folders_are_cleared(tmp_path, monkeypatch):
    import os
    import tempfile
    import time

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    old = tmp_path / "stemn-old"
    old.mkdir()
    (old / "stem_kick.wav").write_bytes(b"x" * 10)
    past = time.time() - 3 * 3600
    os.utime(old, (past, past))
    fresh = tmp_path / "stemn-fresh"
    fresh.mkdir()
    assert gui.clear_stale_work() == 1
    assert not old.exists() and fresh.exists()
