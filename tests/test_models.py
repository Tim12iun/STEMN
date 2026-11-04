from pathlib import Path

import pytest
import yaml

from stemn.models import (
    DEFAULT_MODEL_ID,
    ModelError,
    canonical_stem,
    discover_models,
    get_model,
    import_model,
    load_manifest,
)

REPO_MODELS = Path(__file__).resolve().parents[1] / "models"


def test_canonical_stem_aliases():
    assert canonical_stem("hh") == "hihat"
    assert canonical_stem("Bombo") == "kick"
    assert canonical_stem("Redoblante") == "snare"
    assert canonical_stem("Platillos") == "cymbals"
    assert canonical_stem("Weird Stem!") == "weird-stem"


def test_builtin_manifest_reads_stems_from_config():
    spec = load_manifest(REPO_MODELS / DEFAULT_MODEL_ID, builtin=True)
    assert spec.arch == "mdxc"
    assert spec.canonical_stems == ["kick", "snare", "toms", "hihat", "ride", "crash"]


def _write_model(root: Path, model_id: str, stems=("kick", "snare")) -> Path:
    folder = root / model_id
    folder.mkdir(parents=True)
    (folder / "m.ckpt").write_bytes(b"x")
    (folder / "c.yaml").write_text(yaml.safe_dump({"training": {"instruments": list(stems)}}))
    (folder / "stemn-model.yaml").write_text(
        yaml.safe_dump({"id": model_id, "arch": "mdxc", "checkpoint": "m.ckpt", "config": "c.yaml"})
    )
    return folder


def test_user_models_do_not_shadow_builtins(tmp_path):
    builtin, user = tmp_path / "builtin", tmp_path / "user"
    _write_model(builtin, "a", stems=("kick",))
    _write_model(user, "a", stems=("snare",))
    _write_model(user, "b")
    models = discover_models([(builtin, True), (user, False)])
    assert set(models) == {"a", "b"}
    assert models["a"].builtin and models["a"].canonical_stems == ["kick"]


def test_broken_manifest_is_skipped(tmp_path, capsys):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "stemn-model.yaml").write_text("arch: nope\ncheckpoint: x\nconfig: y\n")
    assert discover_models([(tmp_path, False)]) == {}
    assert "uebersprungen" in capsys.readouterr().err


def test_get_model_errors(tmp_path):
    folder = _write_model(tmp_path, "a")
    models = discover_models([(tmp_path, False)])
    assert get_model(None, models).id == "a"
    with pytest.raises(ModelError):
        get_model("missing", models)
    (folder / "m.ckpt").unlink()
    with pytest.raises(ModelError):
        get_model("a", models)


def test_import_model(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "my.ckpt").write_bytes(b"weights")
    (src / "my.yaml").write_text(yaml.safe_dump({"training": {"instruments": ["kick", "hh"]}}))
    spec = import_model(src / "my.ckpt", src / "my.yaml", name="Mein Modell", target_root=tmp_path / "user")
    assert spec.id == "mein-modell"
    assert spec.canonical_stems == ["kick", "hihat"]
    assert spec.is_available()
    with pytest.raises(ModelError):
        import_model(src / "my.ckpt", src / "my.yaml", name="Mein Modell", target_root=tmp_path / "user")


def test_import_rejects_config_without_stems(tmp_path):
    (tmp_path / "m.ckpt").write_bytes(b"x")
    (tmp_path / "c.yaml").write_text("audio: {}\n")
    with pytest.raises(ModelError):
        import_model(tmp_path / "m.ckpt", tmp_path / "c.yaml", target_root=tmp_path / "user")
