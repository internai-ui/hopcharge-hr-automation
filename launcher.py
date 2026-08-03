"""
launcher.py — the entry point for the one-click bundle.

What it does, in plain terms:
  1. Prints where the user's files live (so they know where to put neon.env).
  2. Starts the dashboard's web server in the background.
  3. Waits until the server is actually answering.
  4. Opens the dashboard in the default web browser automatically.
  5. Keeps running until the window is closed.

This is what makes the bundle feel like an app: the person double-clicks an
icon and the dashboard appears in their browser — no terminal commands.
"""

import sys
import time
import threading
import webbrowser
import socket

HOST = "127.0.0.1"
PORT = 8000
URL = f"http://{HOST}:{PORT}"


def _port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


def _wait_then_open():
    # Poll until the server responds (up to ~30s), then open the browser once.
    for _ in range(75):
        if _port_open(HOST, PORT):
            try:
                webbrowser.open(URL)
            except Exception:
                pass
            return
        time.sleep(0.4)


def main():
    # Friendly banner so a non-technical user can see it's working and find
    # their data folder.
    try:
        from app_paths import describe, DATA_HOME
        print("=" * 60)
        print("  HopCharge HR Dashboard")
        print("=" * 60)
        print(describe())
        print(f"  Opening {URL} in your browser…")
        print("  (Keep this window open while you use the dashboard.)")
        print("  To stop: close this window.")
        print("=" * 60)
    except Exception:
        print(f"Starting HopCharge HR Dashboard at {URL} …")

    # Open the browser shortly after the server comes up.
    threading.Thread(target=_wait_then_open, daemon=True).start()

    # Import the FastAPI app and run it. Importing here (not at top) means the
    # banner shows first and any import error is easier to see.
    import uvicorn
    # The app module is app.py → object `app`.
    from app import app as fastapi_app
    uvicorn.run(fastapi_app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        # On a bundle, a crash would otherwise close the window instantly with no
        # message. Hold the window open so the user can read the error.
        print("\n\nSomething went wrong starting the dashboard:\n")
        import traceback
        traceback.print_exc()
        print("\nThe most common cause is a missing or incorrect neon.env.")
        try:
            from app_paths import NEON_ENV
            print(f"Check that this file exists and is correct:\n  {NEON_ENV}")
        except Exception:
            pass
        input("\nPress Enter to close this window…")
        sys.exit(1)
