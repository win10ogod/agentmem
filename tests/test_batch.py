from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from agentmem.batch import run_batch
from agentmem.store import AgentMemStore


def test_batch_happy_path(tmp_path: Path) -> None:
    store = AgentMemStore(tmp_path / "mem")
    add_req = {"op": "add", "kind": "fact", "tags": ["t"], "text": "hello"}
    inp = StringIO(
        "\n".join(
            [
                json.dumps({"op": "init"}, ensure_ascii=False),
                json.dumps(add_req, ensure_ascii=False),
                json.dumps({"op": "recall", "query": "hello", "limit": 5}, ensure_ascii=False),
                json.dumps({"op": "list", "limit": 10}, ensure_ascii=False),
                "",
            ]
        )
    )
    out = StringIO()
    code = run_batch(store, inp, out, stop_on_error=True, echo=False)
    assert code == 0

    lines = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    assert lines[0]["ok"] is True and lines[0]["op"] == "init"
    mid = lines[1]["result"]["id"]
    assert mid

    hits = lines[2]["result"]
    assert hits and hits[0]["entry"]["id"] == mid

    entries = lines[3]["result"]
    assert entries and entries[0]["id"] == mid


def test_batch_stop_on_error(tmp_path: Path) -> None:
    store = AgentMemStore(tmp_path / "mem")
    inp = StringIO('{"op":"recall","query":""}\n{"op":"add","text":"x"}\n')
    out = StringIO()
    code = run_batch(store, inp, out, stop_on_error=True, echo=False)
    assert code == 2
    first = json.loads(out.getvalue().splitlines()[0])
    assert first["ok"] is False
