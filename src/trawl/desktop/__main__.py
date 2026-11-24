"""Start the desktop app: a tunnel to the server's database, the API on 127.0.0.1, and a window.

`python -m trawl.desktop` (or `trawl app`, or trawl.app from `macos/build.sh`). With TRAWL_SSH_HOST
set (in the checkout's .env), the database is the server's, reached through `ssh -L` for as long
as the app is open; without it, TRAWL_DATABASE_URL as usual. `--no-window` serves the page for a
browser instead, for development.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import threading
import time

from ..config import REPO, Settings, load_dotenv
from ..store import Store

WINDOW = (1320, 860)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(port: int, timeout: float, proc: subprocess.Popen | None = None) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


class Tunnel:
    """`ssh -L` to the server's Postgres, closed with the app. BatchMode: an app has no terminal to
    type a password into, so it fails instead of hanging.

    Quitting the window (⌘Q) or a kill ends the process without running any Python, so nothing here
    could close ssh. Instead ssh runs `cat` on the server, reading a pipe only this process holds:
    however the app ends, the pipe closes, `cat` ends and ssh with it."""

    def __init__(self, host: str) -> None:
        self.port = free_port()
        self.proc = subprocess.Popen(
            ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
             "-o", "ServerAliveInterval=30", "-L", f"{self.port}:127.0.0.1:5432", host, "cat"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if not wait_for(self.port, 20, self.proc):
            self.close()
            raise RuntimeError(f"can't reach the database through ssh {host}: "
                               f"{(self.proc.stderr.read() if self.proc.stderr else '').strip()}")

    def url(self) -> str:
        return f"postgresql://trawl:trawl@localhost:{self.port}/trawl"

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="trawl app", description="the trawl desktop app")
    parser.add_argument("--no-window", action="store_true", help="serve the page for a browser instead")
    parser.add_argument("--port", type=int, default=0, help="port for the page (default: any free one)")
    args = parser.parse_args(argv)

    load_dotenv(REPO / ".env")  # opened from Finder, the working directory is /
    os.chdir(REPO)
    settings = Settings.from_env()
    tunnel = Tunnel(host) if (host := os.environ.get("TRAWL_SSH_HOST")) else None
    try:
        store = Store.connect(tunnel.url() if tunnel else settings.database_url)
        serve(store, settings, args.port or free_port(), window=not args.no_window)
    finally:
        if tunnel:
            tunnel.close()


def serve(store: Store, settings: Settings, port: int, *, window: bool) -> None:
    import uvicorn

    from .server import create_app

    server = uvicorn.Server(uvicorn.Config(create_app(store, settings), host="127.0.0.1", port=port,
                                           log_level="warning"))
    url = f"http://127.0.0.1:{port}/"
    if not window:
        print(f"trawl: {url}")
        server.run()
        return
    import webview

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    if not wait_for(port, 10):
        sys.exit("trawl: the page didn't start")
    webview.create_window("trawl", url, width=WINDOW[0], height=WINDOW[1], min_size=(960, 600))
    webview.start()
    server.should_exit = True
    thread.join(timeout=5)


if __name__ == "__main__":
    main()
