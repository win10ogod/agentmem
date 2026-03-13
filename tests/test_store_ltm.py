from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agentmem.store import AgentMemStore


def test_ltm_add_list_update_forget(tmp_path: Path) -> None:
    home = tmp_path / "mem"
    store = AgentMemStore(home)

    mid = store.add_ltm(text="我偏好純文本記憶。", kind="preference", tags="user,cli", importance=7)
    active = store.load_ltm()
    assert [e.id for e in active] == [mid]
    assert active[0].kind == "preference"
    assert "cli" in active[0].tags

    store.update_ltm(mid, patch={"text": "我偏好用純文本管理記憶。", "importance": 8})
    active2 = store.load_ltm()
    assert active2[0].text == "我偏好用純文本管理記憶。"
    assert active2[0].importance == 8
    assert active2[0].updated_at is not None

    store.forget_ltm(mid, reason="outdated")
    assert store.load_ltm() == []
    all_entries = store.load_ltm(include_inactive=True)
    assert len(all_entries) == 1
    assert all_entries[0].forgotten_at is not None
    assert all_entries[0].forget_reason == "outdated"


def test_ltm_as_of_time_travel(tmp_path: Path) -> None:
    home = tmp_path / "mem"
    store = AgentMemStore(home)

    mid = store.add_ltm(text="A")
    before_update = datetime.now(UTC)
    store.update_ltm(mid, patch={"text": "B"})

    past = store.load_ltm(as_of=before_update, include_inactive=True)
    assert past[0].text == "A"
