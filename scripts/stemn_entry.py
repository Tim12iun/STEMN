"""Entry point for the packaged app (PyInstaller needs an absolute import)."""

from stemn.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
