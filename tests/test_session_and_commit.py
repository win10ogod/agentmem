from __future__ import annotations

from pathlib import Path

from agentmem.store import AgentMemStore


def test_session_commit_auto(tmp_path: Path) -> None:
    store = AgentMemStore(tmp_path / "mem")
    sid = store.start_session()
    store.add_session_message(sid, role="user", text="之後請都用繁體中文回覆。")
    added = store.commit_session_auto(sid)
    assert len(added) == 1
    ltm = store.load_ltm()
    assert len(ltm) == 1
    assert "繁體中文" in ltm[0].text

