from stemn import m4l


def _bundle(tmp_path, text="v1"):
    src = tmp_path / "bundle"
    src.mkdir(exist_ok=True)
    for name in m4l.FILES:
        (src / name).write_text(f"{name} {text}")
    return src


def test_install_and_update(tmp_path):
    lib = tmp_path / "User Library"
    lib.mkdir()
    src = _bundle(tmp_path)
    assert sorted(m4l.install_device(lib, src)) == sorted(m4l.FILES)
    assert (m4l.device_dir(lib) / "stemn.amxd").read_text() == "stemn.amxd v1"
    assert m4l.install_device(lib, src) == []  # unchanged: nothing written
    (src / "stemn-ui.js").write_text("new")
    assert m4l.install_device(lib, src) == ["stemn-ui.js"]
    assert (m4l.device_dir(lib) / "stemn-ui.js").read_text() == "new"


def test_no_ableton_or_no_bundle(tmp_path):
    src = _bundle(tmp_path)
    assert m4l.install_device(tmp_path / "missing", src) == []
    lib = tmp_path / "User Library"
    lib.mkdir()
    assert m4l.install_device(lib, tmp_path / "nothing") == []
    assert not m4l.device_dir(lib).exists()


def test_own_folder_next_to_loomn_devices(tmp_path, monkeypatch):
    monkeypatch.setattr(m4l.Path, "home", lambda: tmp_path)
    assert m4l.device_dir() == tmp_path / "Music/Ableton/User Library/Presets/Audio Effects/Max Audio Effect/stemn"
    (tmp_path / "Documents/MAX for LIVE 3rd Party/LOOMN-MAX4LIVE").mkdir(parents=True)
    assert m4l.device_dir() == tmp_path / "Documents/MAX for LIVE 3rd Party/LOOMN-MAX4LIVE/stemn"
