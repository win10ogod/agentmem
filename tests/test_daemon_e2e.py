from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from agentmem.daemon import load_state, send_request


def _wait_for_file(path: Path, *, timeout_s: float = 3.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size > 0:
            return
        time.sleep(0.05)
    raise TimeoutError(f"timed out waiting for {path}")


def test_daemon_serve_ping_stop(tmp_path: Path) -> None:
    home = tmp_path / "home"
    state_file = tmp_path / "daemon.json"

    env = os.environ.copy()
    env["AGENTMEM_HOME"] = str(home)

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "agentmem",
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--state-file",
            str(state_file),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    try:
        _wait_for_file(state_file)
        state = load_state(state_file)

        resp = send_request(state, {"op": "ping"})
        assert resp.get("ok") is True

        # Server executes normal ops too (same protocol as batch)
        add = send_request(
            state,
            {"op": "add", "kind": "fact", "tags": ["daemon"], "text": "hello daemon"},
        )
        assert add.get("ok") is True
        mid = add["result"]["id"]

        recall = send_request(state, {"op": "recall", "query": "hello", "limit": 3})
        assert recall.get("ok") is True
        assert recall["result"][0]["entry"]["id"] == mid

        stop = send_request(state, {"op": "shutdown"})
        assert stop.get("ok") is True

        proc.wait(timeout=5)
        assert proc.returncode == 0
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
