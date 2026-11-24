"""The app's ssh tunnel ends with the app, however the app ends."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

# Stands in for ssh running `cat`: listens on the -L port, records its pid, ends when its stdin closes
FAKE_SSH = """#!{python}
import os, socket, sys
assert sys.argv[-2:] == ["user@host", "cat"]
port = int(sys.argv[sys.argv.index("-L") + 1].split(":")[0])
s = socket.socket()
s.bind(("127.0.0.1", port))
s.listen()
with open({pid!r}, "w") as f:
    f.write(str(os.getpid()))
sys.stdin.read()
"""
APP = """
import time
from trawl.desktop.__main__ import Tunnel
tunnel = Tunnel("user@host")
print("up", flush=True)
time.sleep(60)
"""


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGKILL])
def test_the_tunnel_ends_with_the_app(tmp_path: Path, sig: signal.Signals) -> None:
    pid_file = tmp_path / "pid"
    ssh = tmp_path / "ssh"
    ssh.write_text(FAKE_SSH.format(python=sys.executable, pid=str(pid_file)))
    ssh.chmod(0o755)
    env = os.environ | {"PATH": f"{tmp_path}:{os.environ['PATH']}"}
    app = subprocess.Popen([sys.executable, "-c", textwrap.dedent(APP)], env=env,
                           stdout=subprocess.PIPE, text=True)
    try:
        assert app.stdout.readline() == "up\n"
        pid = int(pid_file.read_text())
        assert alive(pid)
        app.send_signal(sig)
        app.wait(timeout=5)
        deadline = time.monotonic() + 5
        while alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not alive(pid)
    finally:
        app.kill()
        app.stdout.close()
