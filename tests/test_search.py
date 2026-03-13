from __future__ import annotations

from datetime import UTC, datetime

from agentmem.model import MemoryEntry
from agentmem.search import bm25_search, tokenize


def test_tokenize_cjk() -> None:
    tokens = tokenize("純文本記憶 CLI")
    # CJK chars become tokens; latin words are preserved
    assert "純" in tokens
    assert "cli" in tokens


def test_bm25_basic_ranking() -> None:
    now = datetime.now(UTC)
    entries = [
        MemoryEntry(
            id="1",
            created_at=now,
            updated_at=None,
            kind="note",
            tags=(),
            importance=5,
            text="我偏好純文本記憶。",
            source=None,
            expires_at=None,
            session=None,
            forgotten_at=None,
            forget_reason=None,
        ),
        MemoryEntry(
            id="2",
            created_at=now,
            updated_at=None,
            kind="note",
            tags=(),
            importance=5,
            text="我在研究向量資料庫。",
            source=None,
            expires_at=None,
            session=None,
            forgotten_at=None,
            forget_reason=None,
        ),
    ]
    hits = bm25_search(entries, "純文本", limit=5)
    assert hits
    assert hits[0].entry.id == "1"
