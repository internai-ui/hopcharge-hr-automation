"""
launcher_win.py — Windows-friendly entry point for the desktop shortcut.

Why this exists
---------------
The shortcut launches the app with pythonw.exe (no console window) so the
host doesn't see a black terminal. But launcher.py ends with input("Press
Enter…") on a crash — with no console, that would hang invisibly forever.

This wrapper runs the normal launcher and, if anything fails before the
server is up, shows a real Windows message box with the error so the host
knows what happened (almost always: neon.env missing/incorrect, or a
missing dependency).

The shortcut created by the installer points pythonw.exe at THIS file.
If you'd rather keep using launcher.py directly, change Run-HopCharge.vbs
to reference launcher.py instead — but you lose the friendly popup.
"""

import sys
import traceback


def _popup(title: str, message: str) -> None:
    """Best-effort native Windows message box; falls back to stderr."""
    try:
        import ctypes
        # 0x10 = MB_ICONERROR
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)
    except Exception:
        sys.stderr.write(f"{title}\n\n{message}\n")


def main() -> int:
    try:
        # Reuse the existing launcher unchanged. It starts uvicorn and opens
        # the browser. This call blocks until the app window is closed.
        import launcher
        launcher.main()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        tb = traceback.format_exc()
        hint = (
            "The dashboard could not start.\n\n"
            "Most common cause: the neon.env file is missing or incorrect.\n"
            "Check this folder:\n"
            "    %USERPROFILE%\\HopchargeHR\\neon.env\n\n"
            "Technical details:\n\n"
            + tb
        )
        _popup("HopCharge HR Dashboard - Error", hint)
        return 1


if __name__ == "__main__":
    sys.exit(main())
