"""
Launcher script for the Universal Exhibitor Agent Web Interface.
Starts the FastAPI server with uvicorn and opens the dashboard in your default browser.
"""

import os
import socket
import sys
import threading
import time
import webbrowser

# Ensure project root is in Python path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def find_available_port(default_port: int = 8000) -> int:
    """Finds an available local port starting from default_port."""
    for port in range(default_port, default_port + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return default_port


def open_browser(url: str):
    """Waits for server startup and opens the web dashboard."""
    time.sleep(1.2)
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main():
    import uvicorn

    port = find_available_port(8000)
    server_url = f"http://127.0.0.1:{port}"

    print("=" * 64)
    print("  Universal Exhibitor Agent - Modern Web Dashboard")
    print("=" * 64)
    print(f"  * Web Dashboard URL : {server_url}")
    print(f"  * Live API Docs     : {server_url}/docs")
    print(f"  * Press Ctrl+C to stop the server")
    print("=" * 64)

    # Launch browser in a background thread
    threading.Thread(target=open_browser, args=(server_url,), daemon=True).start()

    # Start uvicorn server
    uvicorn.run("app.ui.server:app", host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
