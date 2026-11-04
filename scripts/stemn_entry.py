"""Entry point for the packaged app (PyInstaller needs an absolute import).

Without arguments (double-click) the window opens; with a command it is the CLI.
"""

import sys
from pathlib import Path


def run() -> int:
    import os

    # windowed builds (no console) have no stdout/stderr; printing would crash
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    args = [a for a in sys.argv[1:] if not a.startswith("-psn")]  # macOS Finder adds -psn_...
    if args == ["--selftest-gui"]:  # used by the build to check the packaged window starts
        from PySide6.QtWidgets import QApplication

        from stemn.gui import MainWindow

        app = QApplication([sys.argv[0]])
        win = MainWindow()
        win.show()
        app.processEvents()
        ok = win.model_box.count() > 0
        win.close()
        return 0 if ok else 1
    if not args or (len(args) == 1 and Path(args[0]).is_file()):
        from stemn.gui import main as gui_main

        return gui_main([sys.argv[0], *args])
    from stemn.cli import main

    return main(args)


if __name__ == "__main__":
    raise SystemExit(run())
