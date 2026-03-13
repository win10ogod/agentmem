from __future__ import annotations

import contextlib
import json
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from .model import MemoryEntry, MemoryKind, SessionMessage
from .utils import (
    FileFingerprint,
    FileLock,
    append_jsonl,
    parse_iso_datetime,
    read_json,
    read_jsonl,
    utc_now_iso,
    write_json,
)

if TYPE_CHECKING:
    from .search import DocStats


class AgentMemError(RuntimeError):
    pass


def _coerce_tags(tags: Iterable[str] | str | None) -> tuple[str, ...]:
    if tags is None:
        return ()
    if isinstance(tags, str):
        if not tags.strip():
            return ()
        parts = [p.strip() for p in tags.split(",")]
        return tuple(p for p in parts if p)
    return tuple(str(t).strip() for t in tags if str(t).strip())


def _coerce_kind(kind: str | None) -> MemoryKind:
    k = (kind or "other").strip().lower()
    if k in {"fact", "preference", "instruction", "profile", "note", "task", "other"}:
        return k  # type: ignore[return-value]
    return "other"


def _coerce_importance(value: int | str | None) -> int:
    if value is None:
        return 5
    try:
        n = int(value)
    except ValueError:
        return 5
    return max(0, min(10, n))


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


@dataclass(frozen=True, slots=True)
class StorePaths:
    home: Path
    ltm_events: Path
    ltm_lock: Path
    cache_dir: Path
    ltm_state_cache: Path
    ltm_state_meta: Path
    ltm_search_cache: Path
    ltm_search_meta: Path
    stm_sessions_dir: Path


def resolve_paths(home: Path) -> StorePaths:
    h = home.expanduser()
    return StorePaths(
        home=h,
        ltm_events=h / "ltm.ndjson",
        ltm_lock=h / "ltm.ndjson.lock",
        cache_dir=h / "cache",
        ltm_state_cache=h / "cache" / "ltm_state.ndjson",
        ltm_state_meta=h / "cache" / "ltm_state.meta.json",
        ltm_search_cache=h / "cache" / "ltm_search.ndjson",
        ltm_search_meta=h / "cache" / "ltm_search.meta.json",
        stm_sessions_dir=h / "stm" / "sessions",
    )


class AgentMemStore:
    """Pure-text, event-sourced memory store (LTM + STM)."""

    def __init__(self, home: Path) -> None:
        self.paths = resolve_paths(home)

    def init_layout(self) -> None:
        self.paths.home.mkdir(parents=True, exist_ok=True)
        self.paths.cache_dir.mkdir(parents=True, exist_ok=True)
        self.paths.stm_sessions_dir.mkdir(parents=True, exist_ok=True)
        self.paths.ltm_events.parent.mkdir(parents=True, exist_ok=True)
        self.paths.ltm_events.touch(exist_ok=True)

    # -----------------
    # LTM (long-term)
    # -----------------
    def add_ltm(
        self,
        *,
        text: str,
        kind: str | None = None,
        tags: Iterable[str] | str | None = None,
        importance: int | str | None = None,
        source: str | None = None,
        expires_at: str | None = None,
        session: str | None = None,
        entry_id: str | None = None,
        created_at: str | None = None,
    ) -> str:
        self.init_layout()
        text = text.strip()
        if not text:
            raise AgentMemError("text cannot be empty")

        entry_id = entry_id or uuid.uuid4().hex
        ts = created_at or utc_now_iso()

        event: dict[str, Any] = {
            "op": "add",
            "ts": ts,
            "id": entry_id,
            "kind": _coerce_kind(kind),
            "tags": list(_coerce_tags(tags)),
            "importance": _coerce_importance(importance),
            "text": text,
            "source": source,
            "expires_at": expires_at,
            "session": session,
        }

        with FileLock(self.paths.ltm_lock):
            append_jsonl(self.paths.ltm_events, event)
        self._invalidate_ltm_caches()
        return entry_id

    def forget_ltm(self, entry_id: str, *, reason: str | None = None) -> None:
        self.init_layout()
        entry_id = entry_id.strip()
        if not entry_id:
            raise AgentMemError("id cannot be empty")
        event: dict[str, Any] = {
            "op": "forget",
            "ts": utc_now_iso(),
            "id": entry_id,
            "reason": reason,
        }
        with FileLock(self.paths.ltm_lock):
            append_jsonl(self.paths.ltm_events, event)
        self._invalidate_ltm_caches()

    def update_ltm(
        self,
        entry_id: str,
        *,
        patch: dict[str, Any],
        reason: str | None = None,
    ) -> None:
        self.init_layout()
        entry_id = entry_id.strip()
        if not entry_id:
            raise AgentMemError("id cannot be empty")
        if not patch:
            raise AgentMemError("patch cannot be empty")
        event: dict[str, Any] = {
            "op": "update",
            "ts": utc_now_iso(),
            "id": entry_id,
            "patch": patch,
            "reason": reason,
        }
        with FileLock(self.paths.ltm_lock):
            append_jsonl(self.paths.ltm_events, event)
        self._invalidate_ltm_caches()

    def load_ltm(
        self,
        *,
        as_of: datetime | None = None,
        include_inactive: bool = False,
    ) -> list[MemoryEntry]:
        """Load LTM state (replayed from events).

        - If as_of is None and cache is valid, use cache.
        - include_inactive includes forgotten/expired entries.
        """

        self.init_layout()
        if as_of is None:
            cached = self._load_ltm_cache()
            if cached is not None:
                return self._filter_active(cached, include_inactive=include_inactive)

        state = self._replay_ltm_events(as_of=as_of)
        if as_of is None:
            self._write_ltm_cache(state)
        return self._filter_active(state, include_inactive=include_inactive, at=as_of)

    def get_ltm(
        self,
        entry_id: str,
        *,
        as_of: datetime | None = None,
        include_inactive: bool = False,
    ) -> MemoryEntry:
        """Get a single LTM entry by id.

        Raises AgentMemError if not found or inactive (unless include_inactive=True).
        """
        entry_id = entry_id.strip()
        if not entry_id:
            raise AgentMemError("id cannot be empty")

        all_entries = self.load_ltm(as_of=as_of, include_inactive=True)
        for e in all_entries:
            if e.id != entry_id:
                continue
            if include_inactive:
                return e
            at = as_of or datetime.now(UTC)
            if e.is_active_at(at):
                return e
            raise AgentMemError(f"entry is inactive: {entry_id} (use --include-inactive)")

        raise AgentMemError(f"entry not found: {entry_id}")

    def _filter_active(
        self,
        entries: list[MemoryEntry],
        *,
        include_inactive: bool,
        at: datetime | None = None,
    ) -> list[MemoryEntry]:
        if include_inactive:
            return entries

        t = at or datetime.now(UTC)
        return [e for e in entries if e.is_active_at(t)]

    def _replay_ltm_events(self, *, as_of: datetime | None) -> list[MemoryEntry]:
        events = read_jsonl(self.paths.ltm_events)
        entries: dict[str, MemoryEntry] = {}

        for ev in events:
            op = str(ev.get("op") or "")
            ts_raw = ev.get("ts")
            if ts_raw is None:
                continue
            try:
                ts = parse_iso_datetime(str(ts_raw))
            except ValueError:
                continue
            if as_of is not None and ts > as_of:
                continue

            entry_id = str(ev.get("id") or "").strip()
            if not entry_id:
                continue

            if op == "add":
                expires_at_raw = ev.get("expires_at")
                expires_at = None
                if expires_at_raw not in (None, ""):
                    expires_at = parse_iso_datetime(str(expires_at_raw))
                entry = MemoryEntry(
                    id=entry_id,
                    created_at=ts,
                    updated_at=None,
                    kind=_coerce_kind(str(ev.get("kind") or "other")),
                    tags=_coerce_tags(ev.get("tags")),
                    importance=_coerce_importance(ev.get("importance")),
                    text=str(ev.get("text") or ""),
                    source=(None if ev.get("source") in (None, "") else str(ev["source"])),
                    expires_at=expires_at,
                    session=(None if ev.get("session") in (None, "") else str(ev["session"])),
                    forgotten_at=None,
                    forget_reason=None,
                )
                entries[entry_id] = entry
                continue

            current = entries.get(entry_id)
            if current is None:
                continue

            if op == "update":
                patch = ev.get("patch") or {}
                if not isinstance(patch, dict):
                    continue
                entries[entry_id] = _apply_patch(current, patch=patch, updated_at=ts)
                continue

            if op == "forget":
                reason = None if ev.get("reason") in (None, "") else str(ev["reason"])
                entries[entry_id] = MemoryEntry(
                    id=current.id,
                    created_at=current.created_at,
                    updated_at=current.updated_at,
                    kind=current.kind,
                    tags=current.tags,
                    importance=current.importance,
                    text=current.text,
                    source=current.source,
                    expires_at=current.expires_at,
                    session=current.session,
                    forgotten_at=ts,
                    forget_reason=reason,
                )
                continue

        # Stable ordering: newest first
        return sorted(entries.values(), key=lambda e: e.created_at, reverse=True)

    def _invalidate_ltm_caches(self) -> None:
        with contextlib.suppress(OSError):
            self.paths.ltm_state_meta.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            self.paths.ltm_search_meta.unlink(missing_ok=True)

    def _load_ltm_cache(self) -> list[MemoryEntry] | None:
        if not self.paths.ltm_state_cache.exists() or not self.paths.ltm_state_meta.exists():
            return None
        meta = read_json(self.paths.ltm_state_meta)
        if meta is None:
            return None
        fp_raw = meta.get("events_fingerprint")
        if not isinstance(fp_raw, dict):
            return None
        try:
            cached_fp = FileFingerprint.from_json(fp_raw)
        except Exception:
            return None
        current_fp = FileFingerprint.from_path(self.paths.ltm_events)
        if cached_fp != current_fp:
            return None

        items = read_jsonl(self.paths.ltm_state_cache)
        return [MemoryEntry.from_dict(i) for i in items]

    def _write_ltm_cache(self, entries: list[MemoryEntry]) -> None:
        self.paths.cache_dir.mkdir(parents=True, exist_ok=True)
        # Write NDJSON state
        self.paths.ltm_state_cache.parent.mkdir(parents=True, exist_ok=True)
        with self.paths.ltm_state_cache.open("w", encoding="utf-8", newline="\n") as f:
            for e in entries:
                f.write(json.dumps(e.to_dict(), ensure_ascii=False, separators=(",", ":")))
                f.write("\n")
        meta: dict[str, Any] = {
            "created_at": utc_now_iso(),
            "events_fingerprint": FileFingerprint.from_path(self.paths.ltm_events).to_json(),
        }
        write_json(self.paths.ltm_state_meta, meta)

    @dataclass(frozen=True, slots=True)
    class CompactResult:
        output_path: Path
        backup_path: Path | None
        entries_total: int
        entries_kept: int
        events_written: int
        drop_inactive: bool
        dry_run: bool

        def to_dict(self) -> dict[str, Any]:
            return {
                "output_path": str(self.output_path),
                "backup_path": (str(self.backup_path) if self.backup_path else None),
                "entries_total": self.entries_total,
                "entries_kept": self.entries_kept,
                "events_written": self.events_written,
                "drop_inactive": self.drop_inactive,
                "dry_run": self.dry_run,
            }

    def compact_ltm(
        self,
        *,
        drop_inactive: bool = False,
        backup: bool = True,
        dry_run: bool = False,
    ) -> CompactResult:
        """Compact the LTM event log into a minimal equivalent log.

        - Keeps current state; rewrites as one `add` per entry
          + optional `update` + optional `forget`.
        - If backup=True, moves old `ltm.ndjson` to a timestamped .bak file.
        - If drop_inactive=True, removes forgotten/expired entries from the new log.
        """
        self.init_layout()
        with FileLock(self.paths.ltm_lock):
            # Replay full state from events (cache is ok; validated by fingerprint)
            entries = self.load_ltm(include_inactive=True)
            total = len(entries)
            if drop_inactive:
                now = datetime.now(UTC)
                entries = [e for e in entries if e.is_active_at(now)]
            kept = len(entries)

            events = _render_compacted_events(entries)
            events_written = len(events)

            backup_path: Path | None = None
            if backup:
                stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                backup_path = self.paths.home / f"ltm.ndjson.bak-{stamp}"
                if backup_path.exists():
                    backup_path = self.paths.home / f"ltm.ndjson.bak-{stamp}-{uuid.uuid4().hex[:8]}"

            if dry_run:
                return self.CompactResult(
                    output_path=self.paths.ltm_events,
                    backup_path=backup_path,
                    entries_total=total,
                    entries_kept=kept,
                    events_written=events_written,
                    drop_inactive=drop_inactive,
                    dry_run=True,
                )

            tmp_path = self.paths.home / f"ltm.ndjson.compact-{uuid.uuid4().hex}.tmp"
            with tmp_path.open("w", encoding="utf-8", newline="\n") as f:
                for ev in events:
                    f.write(json.dumps(ev, ensure_ascii=False, separators=(",", ":")))
                    f.write("\n")

            if backup and backup_path is not None:
                self.paths.ltm_events.replace(backup_path)

            tmp_path.replace(self.paths.ltm_events)

        self._invalidate_ltm_caches()
        return self.CompactResult(
            output_path=self.paths.ltm_events,
            backup_path=backup_path,
            entries_total=total,
            entries_kept=kept,
            events_written=events_written,
            drop_inactive=drop_inactive,
            dry_run=False,
        )

    def load_ltm_docstats(self, *, include_inactive: bool = False) -> list[DocStats]:
        """Load precomputed LTM doc stats for faster BM25.

        This cache is derived data (safe to delete) and is invalidated whenever
        `ltm.ndjson` changes.
        """
        self.init_layout()
        cached = self._load_ltm_search_cache()
        if cached is None:
            # Build from current state (include inactive so we can filter per-call)
            entries = self.load_ltm(include_inactive=True)
            from .search import build_doc_stats

            cached = build_doc_stats(entries)
            self._write_ltm_search_cache(cached)

        if include_inactive:
            return cached

        now = datetime.now(UTC)
        return [d for d in cached if d.entry.is_active_at(now)]

    def _load_ltm_search_cache(self) -> list[DocStats] | None:
        if not self.paths.ltm_search_cache.exists() or not self.paths.ltm_search_meta.exists():
            return None
        meta = read_json(self.paths.ltm_search_meta)
        if meta is None:
            return None
        if meta.get("tokenizer") != "v1":
            return None
        fp_raw = meta.get("events_fingerprint")
        if not isinstance(fp_raw, dict):
            return None
        try:
            cached_fp = FileFingerprint.from_json(fp_raw)
        except Exception:
            return None
        if cached_fp != FileFingerprint.from_path(self.paths.ltm_events):
            return None

        from .search import DocStats

        docs: list[DocStats] = []
        for obj in read_jsonl(self.paths.ltm_search_cache):
            entry_raw = obj.get("entry")
            tf_raw = obj.get("tf")
            if not isinstance(entry_raw, dict) or not isinstance(tf_raw, dict):
                continue
            try:
                entry = MemoryEntry.from_dict(entry_raw)
            except Exception:
                continue
            try:
                dl = int(obj.get("dl", 0))
            except (TypeError, ValueError):
                dl = 0
            tf: dict[str, int] = {}
            for k, v in tf_raw.items():
                if not isinstance(k, str):
                    continue
                try:
                    tf[k] = int(v)
                except (TypeError, ValueError):
                    continue
            docs.append(DocStats(entry=entry, dl=dl, tf=tf))
        return docs

    def _write_ltm_search_cache(self, docs: list[DocStats]) -> None:
        self.paths.cache_dir.mkdir(parents=True, exist_ok=True)
        self.paths.ltm_search_cache.parent.mkdir(parents=True, exist_ok=True)
        with self.paths.ltm_search_cache.open("w", encoding="utf-8", newline="\n") as f:
            for d in docs:
                obj: dict[str, Any] = {"entry": d.entry.to_dict(), "dl": d.dl, "tf": d.tf}
                f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
                f.write("\n")
        meta: dict[str, Any] = {
            "created_at": utc_now_iso(),
            "tokenizer": "v1",
            "events_fingerprint": FileFingerprint.from_path(self.paths.ltm_events).to_json(),
        }
        write_json(self.paths.ltm_search_meta, meta)

    # -----------------
    # STM (short-term)
    # -----------------
    def start_session(self) -> str:
        self.init_layout()
        sid = _new_session_id()
        p = self._session_path(sid)
        append_jsonl(p, {"op": "start", "ts": utc_now_iso(), "session": sid})
        return sid

    def add_session_message(
        self, session: str, *, role: Literal["system", "user", "assistant"], text: str
    ) -> None:
        self.init_layout()
        sid = session.strip()
        if not sid:
            raise AgentMemError("session cannot be empty")
        text = text.strip()
        if not text:
            raise AgentMemError("text cannot be empty")
        p = self._session_path(sid)
        append_jsonl(p, {"op": "msg", "ts": utc_now_iso(), "role": role, "text": text})

    def load_session(self, session: str) -> list[SessionMessage]:
        self.init_layout()
        sid = session.strip()
        p = self._session_path(sid)
        if not p.exists():
            raise AgentMemError(f"session not found: {sid}")
        msgs: list[SessionMessage] = []
        for ev in read_jsonl(p):
            if str(ev.get("op") or "") != "msg":
                continue
            try:
                msgs.append(SessionMessage.from_dict(ev))
            except Exception:
                continue
        return msgs

    def commit_session_auto(self, session: str) -> list[str]:
        """Extract durable memories from a session and add them to LTM."""
        msgs = self.load_session(session)
        candidates = extract_durable_memories(msgs)
        if not candidates:
            return []
        existing = self.load_ltm(include_inactive=False)
        seen = {_normalize_text(e.text) for e in existing}

        added_ids: list[str] = []
        for c in candidates:
            if _normalize_text(c.text) in seen:
                continue
            mid = self.add_ltm(
                text=c.text,
                kind=c.kind,
                tags=c.tags,
                importance=c.importance,
                source=f"session:{session}",
                session=session,
            )
            added_ids.append(mid)
            seen.add(_normalize_text(c.text))
        return added_ids

    def _session_path(self, session: str) -> Path:
        return self.paths.stm_sessions_dir / f"{session}.ndjson"


def _new_session_id() -> str:
    # Time-sortable enough; avoid ":" to keep Windows-friendly filenames
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}-{uuid.uuid4().hex[:8]}"


def _apply_patch(entry: MemoryEntry, *, patch: dict[str, Any], updated_at: datetime) -> MemoryEntry:
    kind = entry.kind
    if "kind" in patch and patch["kind"] is not None:
        kind = _coerce_kind(str(patch["kind"]))

    tags = entry.tags
    if "tags" in patch:
        tags = _coerce_tags(patch["tags"])

    importance = entry.importance
    if "importance" in patch:
        importance = _coerce_importance(patch.get("importance"))

    text = entry.text
    if "text" in patch and patch["text"] is not None:
        text = str(patch["text"])

    source = entry.source
    if "source" in patch:
        source = None if patch["source"] in (None, "") else str(patch["source"])

    expires_at = entry.expires_at
    if "expires_at" in patch:
        expires_at_raw = patch["expires_at"]
        expires_at = (
            parse_iso_datetime(str(expires_at_raw)) if expires_at_raw not in (None, "") else None
        )

    return MemoryEntry(
        id=entry.id,
        created_at=entry.created_at,
        updated_at=updated_at,
        kind=kind,
        tags=tags,
        importance=importance,
        text=text,
        source=source,
        expires_at=expires_at,
        session=entry.session,
        forgotten_at=entry.forgotten_at,
        forget_reason=entry.forget_reason,
    )


@dataclass(frozen=True, slots=True)
class DurableCandidate:
    text: str
    kind: MemoryKind
    tags: tuple[str, ...]
    importance: int


_PREFERENCE_PAT = re.compile(r"(偏好|喜歡|希望|請用|請以|麻煩用|不要|不想|請不要)")
_PROFILE_PAT = re.compile(r"(我的名字是|我叫|我住在|我在|我目前在)")


def extract_durable_memories(messages: list[SessionMessage]) -> list[DurableCandidate]:
    """Rule-based extraction (offline) for STM -> LTM promotion."""
    candidates: list[DurableCandidate] = []
    for m in messages:
        if m.role != "user":
            continue
        text = m.text.strip()
        if not text:
            continue

        if _PROFILE_PAT.search(text):
            candidates.append(
                DurableCandidate(text=text, kind="profile", tags=("user", "profile"), importance=7)
            )
            continue

        if _PREFERENCE_PAT.search(text):
            candidates.append(
                DurableCandidate(
                    text=text,
                    kind="preference",
                    tags=("user", "preference"),
                    importance=7,
                )
            )
            continue

        # Default: durable note if message looks like a stable constraint/instruction
        if len(text) <= 160 and ("以後" in text or "之後" in text or "永遠" in text):
            candidates.append(
                DurableCandidate(
                    text=text,
                    kind="instruction",
                    tags=("user",),
                    importance=6,
                )
            )

    # De-dup by normalized text
    seen: set[str] = set()
    uniq: list[DurableCandidate] = []
    for c in candidates:
        n = _normalize_text(c.text)
        if n in seen:
            continue
        seen.add(n)
        uniq.append(c)
    return uniq


def _render_compacted_events(entries: list[MemoryEntry]) -> list[dict[str, Any]]:
    """Render a minimal event log that recreates the provided state."""
    events: list[dict[str, Any]] = []

    def dt(v: datetime | None) -> str | None:
        if v is None:
            return None
        return v.isoformat(timespec="microseconds")

    def dt_seconds(v: datetime | None) -> str | None:
        if v is None:
            return None
        return v.isoformat(timespec="seconds")

    for e in sorted(entries, key=lambda x: x.created_at):
        events.append(
            {
                "op": "add",
                "ts": dt(e.created_at),
                "id": e.id,
                "kind": e.kind,
                "tags": list(e.tags),
                "importance": e.importance,
                "text": e.text,
                "source": e.source,
                "expires_at": dt_seconds(e.expires_at),
                "session": e.session,
            }
        )

        if e.updated_at is not None:
            patch = {
                "kind": e.kind,
                "tags": list(e.tags),
                "importance": e.importance,
                "text": e.text,
                "source": e.source,
                "expires_at": dt_seconds(e.expires_at),
            }
            events.append(
                {
                    "op": "update",
                    "ts": dt(e.updated_at),
                    "id": e.id,
                    "patch": patch,
                    "reason": "compact",
                }
            )

        if e.forgotten_at is not None:
            events.append(
                {
                    "op": "forget",
                    "ts": dt(e.forgotten_at),
                    "id": e.id,
                    "reason": e.forget_reason,
                }
            )

    return events
