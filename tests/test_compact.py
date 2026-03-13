from __future__ import annotations

from pathlib import Path

from agentmem.store import AgentMemStore


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for _ in path.open("r", encoding="utf-8"))


def test_compact_reduces_events_and_preserves_state(tmp_path: Path) -> None:
    store = AgentMemStore(tmp_path / "mem")
    store.init_layout()

    a = store.add_ltm(text="A", kind="note")
    store.update_ltm(a, patch={"text": "B"})
    store.update_ltm(a, patch={"text": "C"})
    store.update_ltm(a, patch={"importance": 9})
    store.forget_ltm(a, reason="cleanup")

    b = store.add_ltm(text="D", kind="fact", tags="x")
    store.update_ltm(b, patch={"tags": ["x", "y"]})
    store.update_ltm(b, patch={"text": "D2"})

    before = _count_lines(store.paths.ltm_events)
    before_state = store.load_ltm(include_inactive=True)
    assert before > 0
    assert len(before_state) == 2

    res = store.compact_ltm(drop_inactive=False, backup=True)
    assert res.backup_path is not None and res.backup_path.exists()

    after = _count_lines(store.paths.ltm_events)
    after_state = store.load_ltm(include_inactive=True)
    assert len(after_state) == len(before_state)
    assert after < before


def test_compact_drop_inactive(tmp_path: Path) -> None:
    store = AgentMemStore(tmp_path / "mem")
    mid = store.add_ltm(text="A")
    store.forget_ltm(mid, reason="bye")
    store.add_ltm(text="B")

    res = store.compact_ltm(drop_inactive=True, backup=False)
    assert res.entries_kept == 1
    active = store.load_ltm()
    assert len(active) == 1
    assert active[0].text == "B"

