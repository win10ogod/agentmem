from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TextIO

from .search import bm25_search, bm25_search_docs
from .store import AgentMemError, AgentMemStore
from .utils import FileFingerprint, is_tty


@dataclass(frozen=True, slots=True)
class BatchOptions:
    stop_on_error: bool = False
    echo: bool = False


def run_batch(
    store: AgentMemStore,
    input_stream: TextIO,
    output_stream: TextIO,
    *,
    stop_on_error: bool,
    echo: bool,
) -> int:
    """Process NDJSON requests from stdin and write NDJSON responses to stdout."""
    if is_tty(input_stream):
        raise AgentMemError("batch expects NDJSON on stdin")

    opts = BatchOptions(stop_on_error=stop_on_error, echo=echo)
    store.init_layout()

    docs_cache: list[Any] | None = None
    docs_fp: FileFingerprint | None = None

    for line_no, line in enumerate(input_stream, start=1):
        line = line.strip()
        if not line:
            continue

        req: dict[str, Any]
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict):
                raise ValueError("request must be a JSON object")
            req = obj
        except Exception as e:
            _write_resp(
                output_stream,
                {"ok": False, "op": None, "error": f"line {line_no}: {e}"},
            )
            if opts.stop_on_error:
                return 2
            continue

        op = req.get("op")
        if not isinstance(op, str) or not op.strip():
            _write_resp(
                output_stream,
                _maybe_echo({"ok": False, "op": None, "error": "missing op"}, req, opts),
            )
            if opts.stop_on_error:
                return 2
            continue

        try:
            result, ltm_mutated, docs_cache, docs_fp = _handle_op(
                store,
                op=op,
                req=req,
                docs_cache=docs_cache,
                docs_fp=docs_fp,
            )
            if ltm_mutated:
                docs_cache = None
                docs_fp = None

            resp = _maybe_echo({"ok": True, "op": op, "result": result}, req, opts)
            _write_resp(output_stream, resp)
        except Exception as e:
            resp = _maybe_echo({"ok": False, "op": op, "error": str(e)}, req, opts)
            _write_resp(output_stream, resp)
            if opts.stop_on_error:
                return 2

    return 0


def _maybe_echo(resp: dict[str, Any], req: dict[str, Any], opts: BatchOptions) -> dict[str, Any]:
    if not opts.echo:
        return resp
    return {**resp, "request": req}


def _write_resp(output_stream: TextIO, resp: dict[str, Any]) -> None:
    output_stream.write(json.dumps(resp, ensure_ascii=False, separators=(",", ":")))
    output_stream.write("\n")
    output_stream.flush()


def _parse_as_of(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("as_of must be an ISO datetime string")
    return datetime.fromisoformat(value)


def _filter_entries(entries: list[Any], *, kind: Any, tags: Any) -> list[Any]:
    k = (str(kind) if kind is not None else "").strip().lower()
    tset = _coerce_tagset(tags)
    out: list[Any] = []
    for e in entries:
        if k and str(getattr(e, "kind", "")).lower() != k:
            continue
        if tset:
            etags = set(getattr(e, "tags", ()))
            if not (tset & etags):
                continue
        out.append(e)
    return out


def _filter_docs(docs: list[Any], *, kind: Any, tags: Any) -> list[Any]:
    k = (str(kind) if kind is not None else "").strip().lower()
    tset = _coerce_tagset(tags)
    out: list[Any] = []
    for d in docs:
        e = getattr(d, "entry", None)
        if e is None:
            continue
        if k and str(getattr(e, "kind", "")).lower() != k:
            continue
        if tset:
            etags = set(getattr(e, "tags", ()))
            if not (tset & etags):
                continue
        out.append(d)
    return out


def _coerce_tagset(value: Any) -> set[str]:
    if value in (None, ""):
        return set()
    if isinstance(value, str):
        return {value.strip()} if value.strip() else set()
    if isinstance(value, list):
        return {str(t).strip() for t in value if str(t).strip()}
    return set()


def _handle_op(
    store: AgentMemStore,
    *,
    op: str,
    req: dict[str, Any],
    docs_cache: list[Any] | None,
    docs_fp: FileFingerprint | None,
) -> tuple[Any, bool, list[Any] | None, FileFingerprint | None]:
    """Return (result, ltm_mutated)."""
    op = op.strip().lower()

    if op == "init":
        store.init_layout()
        return {"home": str(store.paths.home)}, False, docs_cache, docs_fp

    if op == "add":
        text = str(req.get("text") or "")
        source = None if req.get("source") in (None, "") else str(req.get("source"))
        expires_at = None if req.get("expires_at") in (None, "") else str(req.get("expires_at"))
        session = None if req.get("session") in (None, "") else str(req.get("session"))
        entry_id = None if req.get("id") in (None, "") else str(req.get("id"))
        created_at = None if req.get("created_at") in (None, "") else str(req.get("created_at"))
        mid = store.add_ltm(
            text=text,
            kind=(None if req.get("kind") is None else str(req.get("kind"))),
            tags=req.get("tags"),
            importance=req.get("importance"),
            source=source,
            expires_at=expires_at,
            session=session,
            entry_id=entry_id,
            created_at=created_at,
        )
        return {"id": mid}, True, docs_cache, docs_fp

    if op == "update":
        entry_id = str(req.get("id") or "").strip()
        if not entry_id:
            raise AgentMemError("update requires id")
        patch = req.get("patch")
        if patch is None:
            patch = {}
        if not isinstance(patch, dict):
            raise AgentMemError("update.patch must be a JSON object")
        reason = None if req.get("reason") in (None, "") else str(req.get("reason"))
        store.update_ltm(entry_id, patch=patch, reason=reason)
        return {"id": entry_id}, True, docs_cache, docs_fp

    if op == "forget":
        entry_id = str(req.get("id") or "").strip()
        if not entry_id:
            raise AgentMemError("forget requires id")
        reason = None if req.get("reason") in (None, "") else str(req.get("reason"))
        store.forget_ltm(entry_id, reason=reason)
        return {"id": entry_id}, True, docs_cache, docs_fp

    if op == "show":
        entry_id = str(req.get("id") or "").strip()
        include_inactive = bool(req.get("include_inactive", False))
        as_of = _parse_as_of(req.get("as_of"))
        entry = store.get_ltm(entry_id, as_of=as_of, include_inactive=include_inactive)
        return entry.to_dict(), False, docs_cache, docs_fp

    if op == "list":
        include_inactive = bool(req.get("include_inactive", False))
        limit_raw = req.get("limit", 50)
        try:
            limit = max(0, int(limit_raw))
        except (TypeError, ValueError):
            limit = 50
        entries = store.load_ltm(include_inactive=include_inactive)
        entries = entries[:limit]
        return [e.to_dict() for e in entries], False, docs_cache, docs_fp

    if op == "recall":
        query = str(req.get("query") or "")
        if not query.strip():
            raise AgentMemError("recall requires query")
        include_inactive = bool(req.get("include_inactive", False))
        limit_raw = req.get("limit", 10)
        try:
            limit = max(0, int(limit_raw))
        except (TypeError, ValueError):
            limit = 10

        as_of = _parse_as_of(req.get("as_of"))
        kind = req.get("kind")
        tags = req.get("tags") if "tags" in req else req.get("tag")

        if as_of is None:
            fp = FileFingerprint.from_path(store.paths.ltm_events)
            if docs_cache is None or docs_fp != fp:
                docs_cache = store.load_ltm_docstats(include_inactive=include_inactive)
                docs_fp = fp
            docs = docs_cache
            docs = _filter_docs(docs, kind=kind, tags=tags)
            hits = bm25_search_docs(docs, query, limit=limit)
        else:
            entries = store.load_ltm(as_of=as_of, include_inactive=include_inactive)
            entries = _filter_entries(entries, kind=kind, tags=tags)
            hits = bm25_search(entries, query, limit=limit)

        return (
            [
                {
                    "score": h.score,
                    "matched_terms": h.matched_terms,
                    "term_counts": h.term_counts,
                    "entry": h.entry.to_dict(),
                }
                for h in hits
            ],
            False,
            docs_cache,
            docs_fp,
        )

    if op == "compact":
        drop_inactive = bool(req.get("drop_inactive", False))
        backup = bool(req.get("backup", True))
        dry_run = bool(req.get("dry_run", False))
        result = store.compact_ltm(drop_inactive=drop_inactive, backup=backup, dry_run=dry_run)
        return result.to_dict(), True, docs_cache, docs_fp

    raise AgentMemError(f"unknown batch op: {op}")
