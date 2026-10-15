"""LordBlack Harness launcher: starts the backend and opens the UI."""
import argparse
import os
import sys
import threading
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend import __version__  # noqa: E402
from backend.config import get_config  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="LordBlack Harness")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--host", default=None, help="override bind host")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    cfg = get_config()
    host = args.host or ("0.0.0.0" if cfg.get("general.lan_access") else "127.0.0.1")
    port = args.port or int(cfg.get("general.port", 8765))
    url = f"http://127.0.0.1:{port}"

    if not args.no_browser and cfg.get("general.open_browser_on_start", True):
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    print(f"\n  ==============================================")
    print(f"     L o r d B l a c k   H a r n e s s  v{__version__}")
    print(f"  ==============================================")
    print(f"  UI:      {url}")
    print(f"  Network: {host} (LAN access: {'ON' if host == '0.0.0.0' else 'off'})")
    print("  Ctrl+C to stop.\n")

    # pre-flight: uvicorn exits via sys.exit on bind failure, so check the port first
    import socket
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex((probe_host, port)) == 0:
            print(f"  [!] Port {port} is already in use — another LordBlack Harness "
                  "instance is probably still running (check other console windows "
                  "or the system tray).")
            print("      Options: close the other instance, or start on another port:")
            print(f"          python app.py --port {port + 1}\n")
            sys.exit(1)

    import uvicorn
    uvicorn.run("backend.main:app", host=host, port=port, log_level="warning")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
