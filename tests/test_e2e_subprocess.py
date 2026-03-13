from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _run_agentmem(args: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agentmem", *args],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )


def test_e2e_subprocess_with_env_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    env = os.environ.copy()
    env["AGENTMEM_HOME"] = str(home)

    r = _run_agentmem(["init"], env=env)
    assert r.stdout.strip() == str(home)

    r = _run_agentmem(
        ["add", "--kind", "fact", "--tags", "e2e", "--text", "hello from e2e"],
        env=env,
    )
    mid = r.stdout.strip().splitlines()[-1]
    assert mid

    r = _run_agentmem(["show", mid, "--format", "json"], env=env)
    entry = json.loads(r.stdout)
    assert entry["id"] == mid

    r = _run_agentmem(["recall", "hello", "--format", "json", "--limit", "5"], env=env)
    hits = json.loads(r.stdout)
    assert hits and hits[0]["entry"]["id"] == mid

    patch = tmp_path / "mem.patch.toml"
    patch.write_text(
        """format = "agentmem-patch"
version = 1

[[op]]
type = "add"
kind = "note"
tags = ["e2e"]
text = "from patch"
""",
        encoding="utf-8",
    )

    r = _run_agentmem(["patch", "validate", str(patch)], env=env)
    summary = json.loads(r.stdout)
    assert summary["errors"] == []

    _run_agentmem(["patch", "apply", str(patch)], env=env)

    r = _run_agentmem(["list", "--format", "json"], env=env)
    entries = json.loads(r.stdout)
    assert any(e["text"] == "from patch" for e in entries)

    r = _run_agentmem(["compact", "--format", "json"], env=env)
    compact = json.loads(r.stdout)
    assert compact["backup_path"]
    assert Path(compact["backup_path"]).exists()

    # Batch mode
    batch_in = "\n".join(
        [
            json.dumps({"op": "recall", "query": "patch", "limit": 5}, ensure_ascii=False),
            json.dumps({"op": "list", "limit": 5}, ensure_ascii=False),
            "",
        ]
    )
    r = subprocess.run(
        [sys.executable, "-m", "agentmem", "batch"],
        input=batch_in,
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )
    lines = [json.loads(line) for line in r.stdout.splitlines() if line.strip()]
    assert lines[0]["ok"] is True and lines[0]["op"] == "recall"
    assert lines[1]["ok"] is True and lines[1]["op"] == "list"
