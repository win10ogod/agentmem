from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass

from .model import MemoryEntry

_TOKEN_RE = re.compile(
    r"[A-Za-z0-9_]+|"
    r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF\u3040-\u30FF\uAC00-\uD7AF]"
)


def tokenize(text: str) -> list[str]:
    """Tokenize text for multilingual (CJK-friendly) lexical search."""
    return [m.group(0).lower() for m in _TOKEN_RE.finditer(text)]


@dataclass(frozen=True, slots=True)
class SearchHit:
    entry: MemoryEntry
    score: float
    matched_terms: tuple[str, ...]
    term_counts: dict[str, int]


def _term_freq(tokens: list[str]) -> dict[str, int]:
    freq: dict[str, int] = {}
    for t in tokens:
        freq[t] = freq.get(t, 0) + 1
    return freq


def bm25_search(
    entries: Iterable[MemoryEntry],
    query: str,
    *,
    limit: int = 10,
    k1: float = 1.2,
    b: float = 0.75,
) -> list[SearchHit]:
    """BM25 ranker over MemoryEntry.text (pure lexical, deterministic)."""
    query_tokens = tokenize(query)
    if not query_tokens:
        return []

    docs: list[tuple[MemoryEntry, list[str], dict[str, int]]] = []
    lengths: list[int] = []
    for e in entries:
        tokens = tokenize(e.text)
        tf = _term_freq(tokens)
        docs.append((e, tokens, tf))
        lengths.append(len(tokens))

    if not docs:
        return []

    avgdl = (sum(lengths) / len(lengths)) if lengths else 0.0
    # Document frequency for query terms only
    q_terms = set(query_tokens)
    df: dict[str, int] = {t: 0 for t in q_terms}
    for _, _, tf in docs:
        for t in q_terms:
            if t in tf:
                df[t] += 1

    n = len(docs)
    idf: dict[str, float] = {}
    for t, dft in df.items():
        # BM25+ style idf to keep positive
        idf[t] = math.log(1.0 + (n - dft + 0.5) / (dft + 0.5))

    hits: list[SearchHit] = []
    for entry, tokens, tf in docs:
        dl = len(tokens)
        denom_norm = k1 * (1.0 - b + (b * (dl / avgdl if avgdl > 0 else 0.0)))
        score = 0.0
        term_counts: dict[str, int] = {}
        matched: list[str] = []
        for t in query_tokens:
            f = tf.get(t, 0)
            if f <= 0:
                continue
            term_counts[t] = term_counts.get(t, 0) + f
            if t not in matched:
                matched.append(t)
            score += idf.get(t, 0.0) * (f * (k1 + 1.0)) / (f + denom_norm)

        if score > 0:
            hits.append(
                SearchHit(
                    entry=entry,
                    score=score,
                    matched_terms=tuple(matched),
                    term_counts=term_counts,
                )
            )

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[: max(0, limit)]
