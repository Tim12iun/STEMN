"""Entry point for the packaged app (PyInstaller needs an absolute import).

Without arguments (double-click) the window opens; with a command it is the CLI.
"""

import sys
from pathlib import Path


def _hide_from_dock() -> None:
    """macOS: a command-line run (also the separation the window starts in the
    background) must not get its own Dock icon. Mark the process as one that
    never shows up in the Dock before any library can touch the window server."""
    if sys.platform != "darwin":
        return
    try:
        import ctypes
        import ctypes.util

        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AppKit.framework/AppKit")
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        send = objc.objc_msgSend
        send.restype = ctypes.c_void_p
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        app = send(objc.objc_getClass(b"NSApplication"), objc.sel_registerName(b"sharedApplication"))
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
        send(app, objc.sel_registerName(b"setActivationPolicy:"), 2)  # NSApplicationActivationPolicyProhibited
    except Exception:
        pass


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
    _hide_from_dock()
    from stemn.cli import main

    return main(args)


if __name__ == "__main__":
    raise SystemExit(run())
