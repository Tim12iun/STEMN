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
    progress = _progress_writer(args.progress_file) if args.progress_file else None
    if args.progress_file:
        _exit_with_parent()  # started by the app window: never keep working after it is gone
    for path in args.input:
        out = separate(path, stems=stems, model_id=args.model, output_dir=args.output, quality=args.quality, progress=progress, restore=args.restore, bleed=args.bleed)
        results[str(path)] = {k: str(v) for k, v in out.items()}
        if not args.json:
            print(f"{path}:")
            for stem, file in out.items():
                print(f"  {stem:8s} {file}")
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    if args.result_file:
        _write_result(args.result_file, {"stems": results})
    return 0


def _exit_with_parent() -> None:
    import os
    import threading
    import time

    parent = os.getppid()

    def watch() -> None:
        while True:
            time.sleep(1.0)
            if os.getppid() != parent:
                os._exit(3)

    threading.Thread(target=watch, daemon=True).start()


def _progress_writer(path: str):
    target = Path(path)

    def write(value: float) -> None:
        try:
            target.write_text(f"{value:.3f}", encoding="utf-8")
        except OSError:
            pass

    return write


def _write_result(path: str, data: dict) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


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
    s.add_argument("-q", "--quality", default="best", choices=["fast", "normal", "best"], help="Tempo/Qualitaet (Standard: best)")
    s.add_argument("-b", "--bleed", default="off", choices=["off", "light", "strong"], help="Uebersprechen zwischen den Spuren daempfen (Standard: off)")
    s.add_argument("-r", "--restore", action="store_true", help="danach jede Spur mit Apollo reparieren (fehlende Hoehen und Details ergaenzen, langsamer)")
    s.add_argument("--json", action="store_true", help="Ergebnis als JSON ausgeben")
    s.add_argument("--result-file", help=argparse.SUPPRESS)  # used by the app window
    s.add_argument("--progress-file", help=argparse.SUPPRESS)  # used by the app window
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
    u.set_defaults(func=_cmd_update)

    v = sub.add_parser("serve", help="lokalen Dienst fuer Max for Live / Ableton starten")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=47800)
    v.set_defaults(func=_cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result_file = getattr(args, "result_file", None)
    try:
        return args.func(args)
    except (ModelError, FileNotFoundError, ValueError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        if result_file:
            _write_result(result_file, {"error": str(exc)})
        return 2
    except Exception as exc:
        if not result_file:
            raise
        _write_result(result_file, {"error": f"{type(exc).__name__}: {exc}"})
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
