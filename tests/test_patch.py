from __future__ import annotations

from pathlib import Path

from agentmem.patch import apply_patch, load_patch_toml, validate_patch
from agentmem.store import AgentMemStore


def test_patch_add_update_forget(tmp_path: Path) -> None:
    store = AgentMemStore(tmp_path / "mem")
    patch_path = tmp_path / "p.toml"
    patch_path.write_text(
        """format = "agentmem-patch"
version = 1

[[op]]
type = "add"
kind = "fact"
tags = ["project"]
text = "hello world"

[[op]]
type = "update"
id = "PUT_ID"
patch = { text = "hello updated" }

[[op]]
type = "forget"
id = "PUT_ID"
reason = "cleanup"
""",
        encoding="utf-8",
    )

    patch = load_patch_toml(patch_path)
    summary = validate_patch(patch)
    assert not summary.errors

    # Apply add first to get deterministic id, then rewrite patch with that id.
    applied1 = apply_patch(
        store,
        {
            "format": "agentmem-patch",
            "version": 1,
            "op": [patch["op"][0]],
        },
    )
    assert not applied1.errors
    assert applied1.added_ids
    mid = applied1.added_ids[0]

    # Update + forget
    patch["op"][1]["id"] = mid
    patch["op"][2]["id"] = mid
    applied2 = apply_patch(store, patch)
    assert not applied2.errors
    assert store.load_ltm() == []
    inactive = store.load_ltm(include_inactive=True)
    assert inactive and inactive[0].forget_reason == "cleanup"


def test_patch_reapply_is_idempotent_for_add_without_id(tmp_path: Path) -> None:
    store = AgentMemStore(tmp_path / "mem")
    patch = {
        "format": "agentmem-patch",
        "version": 1,
        "op": [
            {"type": "add", "kind": "fact", "tags": ["x"], "text": "same"},
        ],
    }
    a1 = apply_patch(store, patch)
    a2 = apply_patch(store, patch)
    assert not a1.errors
    assert not a2.errors
    assert store.load_ltm()  # still one entry state-wise
    assert store.load_ltm(include_inactive=True)[0].id == a1.added_ids[0]
