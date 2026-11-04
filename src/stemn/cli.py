"""Command line: ``stemn separate``, ``stemn models``, ``stemn serve``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .models import ModelError, discover_models, import_model, user_models_dir


def _cmd_separate(args) -> int:
    from .engine import separate

    stems = [s for s in args.stems.split(",") if s] if args.stems else None
    results = {}
    for path in args.input:
        out = separate(path, stems=stems, model_id=args.model, output_dir=args.output, quality=args.quality)
        results[str(path)] = {k: str(v) for k, v in out.items()}
        if not args.json:
            print(f"{path}:")
            for stem, file in out.items():
                print(f"  {stem:8s} {file}")
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0


def _cmd_models_list(args) -> int:
    models = discover_models()
    if args.json:
        print(json.dumps([m.to_dict() for m in models.values()], indent=2, ensure_ascii=False))
        return 0
    if not models:
        print("Keine Modelle gefunden.")
    for m in models.values():
        flag = "eingebaut" if m.builtin else "importiert"
        state = "" if m.is_available() else "  [Dateien fehlen]"
        print(f"{m.id:24s} {flag:10s} {', '.join(m.canonical_stems)}{state}")
    print(f"\nEigene Modelle liegen in: {user_models_dir()}")
    return 0


def _cmd_models_import(args) -> int:
    spec = import_model(Path(args.checkpoint), Path(args.config), name=args.name, model_id=args.id, arch=args.arch)
    print(f"Importiert: {spec.id} ({', '.join(spec.canonical_stems)}) -> {spec.folder}")
    return 0


def _cmd_update(args) -> int:
    from . import updater

    if args.token:
        cfg = updater.load_config()
        cfg["github_token"] = args.token
        updater.save_config(cfg)
        print(f"Token gespeichert in {updater.config_path()}")
    if args.auto is not None:
        cfg = updater.load_config()
        cfg["auto_update"] = args.auto == "on"
        updater.save_config(cfg)
        print(f"Automatische Updates: {args.auto}")
    try:
        if args.check:
            rel = updater.check()
            print(f"Neue Version {rel.version} verfuegbar." if rel else f"stemn {__version__} ist aktuell.")
        else:
            print(updater.run_update())
    except updater.UpdateError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2
    return 0


def _cmd_serve(args) -> int:
    from .server import serve

    serve(host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="stemn", description="Drum-Loops in Kick, Snare, Hi-Hat usw. zerlegen.")
    p.add_argument("--version", action="version", version=f"stemn {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("separate", help="Audiodatei(en) zerlegen")
    s.add_argument("input", nargs="+", help="WAV/AIFF/FLAC-Datei(en)")
    s.add_argument("-s", "--stems", help="nur diese Stems, z.B. kick,snare (Standard: alle)")
    s.add_argument("-m", "--model", help="Modell-ID (siehe 'stemn models list')")
    s.add_argument("-o", "--output", help="Zielordner (Standard: '<Datei> stems' daneben)")
    s.add_argument("-q", "--quality", default="normal", choices=["fast", "normal", "best"], help="Tempo/Qualitaet (Standard: normal)")
    s.add_argument("--json", action="store_true", help="Ergebnis als JSON ausgeben")
    s.set_defaults(func=_cmd_separate)

    m = sub.add_parser("models", help="Modelle anzeigen oder importieren")
    msub = m.add_subparsers(dest="models_cmd", required=True)
    ml = msub.add_parser("list", help="verfuegbare Modelle")
    ml.add_argument("--json", action="store_true")
    ml.set_defaults(func=_cmd_models_list)
    mi = msub.add_parser("import", help="eigenes Modell hinzufuegen")
    mi.add_argument("checkpoint", help="Modelldatei (.ckpt/.th)")
    mi.add_argument("config", help="passende Config (.yaml)")
    mi.add_argument("--name", help="Anzeigename")
    mi.add_argument("--id", help="Modell-ID (Standard: aus dem Namen)")
    mi.add_argument("--arch", default="mdxc", help="mdxc (MDX23C/Roformer, Standard) oder demucs")
    mi.set_defaults(func=_cmd_models_import)

    u = sub.add_parser("update", help="nach neuer Version suchen und installieren")
    u.add_argument("--check", action="store_true", help="nur pruefen, nichts installieren")
    u.add_argument("--token", help="GitHub-Token (nur Lesezugriff) speichern")
    u.add_argument("--auto", choices=["on", "off"], help="automatische Updates beim Start an/aus")
    u.set_defaults(func=_cmd_update)

    v = sub.add_parser("serve", help="lokalen Dienst fuer Max for Live / Ableton starten")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=47800)
    v.set_defaults(func=_cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ModelError, FileNotFoundError, ValueError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
