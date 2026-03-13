from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .store import AgentMemStore

PatchDict = dict[str, Any]


@dataclass(frozen=True, slots=True)
class PatchSummary:
    ops_total: int
    adds: int
    updates: int
    forgets: int
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ApplyResult:
    added_ids: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


def load_patch_toml(path: Path) -> PatchDict:
    with path.open("rb") as f:
        obj = tomllib.load(f)
    if not isinstance(obj, dict):
        raise ValueError("patch root must be a TOML table")
    return obj


def validate_patch(patch: PatchDict) -> PatchSummary:
    errors: list[str] = []
    fmt = patch.get("format")
    ver = patch.get("version")
    if fmt != "agentmem-patch":
        errors.append('patch.format must be "agentmem-patch"')
    if ver != 1:
        errors.append("patch.version must be 1")

    ops = patch.get("op")
    if ops is None:
        errors.append('patch must contain [[op]] entries ("op" array of tables)')
        return PatchSummary(ops_total=0, adds=0, updates=0, forgets=0, errors=tuple(errors))
    if not isinstance(ops, list):
        errors.append("patch.op must be a list")
        return PatchSummary(ops_total=0, adds=0, updates=0, forgets=0, errors=tuple(errors))

    adds = updates = forgets = 0
    for idx, op in enumerate(ops):
        if not isinstance(op, dict):
            errors.append(f"op[{idx}] must be a table")
            continue
        t = op.get("type")
        if t not in ("add", "update", "forget"):
            errors.append(f'op[{idx}].type must be "add"|"update"|"forget"')
            continue

        if t == "add":
            adds += 1
            _validate_add(op, idx=idx, errors=errors)
        elif t == "update":
            updates += 1
            _validate_update(op, idx=idx, errors=errors)
        else:
            forgets += 1
            _validate_forget(op, idx=idx, errors=errors)

    return PatchSummary(
        ops_total=len(ops),
        adds=adds,
        updates=updates,
        forgets=forgets,
        errors=tuple(errors),
    )


def apply_patch(store: AgentMemStore, patch: PatchDict) -> ApplyResult:
    summary = validate_patch(patch)
    if summary.errors:
        return ApplyResult(errors=summary.errors)

    ops = patch.get("op") or []
    added_ids: list[str] = []
    errors: list[str] = []
    for idx, op in enumerate(ops):
        t = op.get("type")
        try:
            if t == "add":
                added_ids.append(_apply_add(store, op))
            elif t == "forget":
                _apply_forget(store, op)
            elif t == "update":
                _apply_update(store, op)
            else:
                errors.append(f"op[{idx}]: unknown type: {t}")
        except Exception as e:
            errors.append(f"op[{idx}]: {e}")

    return ApplyResult(added_ids=tuple(added_ids), errors=tuple(errors))


def _validate_add(op: dict[str, Any], *, idx: int, errors: list[str]) -> None:
    text = op.get("text")
    if not isinstance(text, str) or not text.strip():
        errors.append(f"op[{idx}].text must be a non-empty string")
    _validate_tags(op.get("tags"), idx=idx, errors=errors)
    _validate_iso(
        op.get("created_at"),
        field=f"op[{idx}].created_at",
        errors=errors,
        allow_none=True,
    )
    _validate_iso(
        op.get("expires_at"),
        field=f"op[{idx}].expires_at",
        errors=errors,
        allow_none=True,
    )
    _validate_optional_str(op.get("id"), field=f"op[{idx}].id", errors=errors)


def _validate_forget(op: dict[str, Any], *, idx: int, errors: list[str]) -> None:
    entry_id = op.get("id")
    if not isinstance(entry_id, str) or not entry_id.strip():
        errors.append(f"op[{idx}].id must be a non-empty string")


def _validate_update(op: dict[str, Any], *, idx: int, errors: list[str]) -> None:
    entry_id = op.get("id")
    if not isinstance(entry_id, str) or not entry_id.strip():
        errors.append(f"op[{idx}].id must be a non-empty string")
    patch = op.get("patch")
    if not isinstance(patch, dict) or not patch:
        errors.append(f"op[{idx}].patch must be a non-empty table")
        return
    if "tags" in patch:
        _validate_tags(patch.get("tags"), idx=idx, errors=errors, prefix=f"op[{idx}].patch")
    if "expires_at" in patch:
        _validate_iso(
            patch.get("expires_at"),
            field=f"op[{idx}].patch.expires_at",
            errors=errors,
            allow_none=True,
        )


def _validate_tags(
    value: Any,
    *,
    idx: int,
    errors: list[str],
    prefix: str | None = None,
) -> None:
    if value is None:
        return
    field = f"{prefix}.tags" if prefix else f"op[{idx}].tags"
    if isinstance(value, str):
        return
    if isinstance(value, list) and all(isinstance(t, str) for t in value):
        return
    errors.append(f"{field} must be a list[str] or comma-separated string")


def _validate_iso(value: Any, *, field: str, errors: list[str], allow_none: bool) -> None:
    if value is None:
        if allow_none:
            return
        errors.append(f"{field} is required")
        return
    if not isinstance(value, str):
        errors.append(f"{field} must be an ISO datetime string")
        return
    try:
        datetime.fromisoformat(value)
    except ValueError:
        errors.append(f"{field} must be ISO-8601 with UTC offset, got: {value}")


def _validate_optional_str(value: Any, *, field: str, errors: list[str]) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        errors.append(f"{field} must be a string")


def _apply_add(store: AgentMemStore, op: dict[str, Any]) -> str:
    entry_id = op.get("id")
    if not isinstance(entry_id, str) or not entry_id.strip():
        entry_id = _deterministic_add_id(op)

    created_at = op.get("created_at")
    if created_at is not None and not isinstance(created_at, str):
        raise ValueError("created_at must be a string")
    expires_at = op.get("expires_at")
    if expires_at is not None and not isinstance(expires_at, str):
        raise ValueError("expires_at must be a string")

    tags = op.get("tags")
    if isinstance(tags, list):
        tags_value: Any = [str(t) for t in tags]
    else:
        tags_value = tags

    return store.add_ltm(
        text=str(op.get("text") or ""),
        kind=(None if op.get("kind") is None else str(op.get("kind"))),
        tags=tags_value,
        importance=op.get("importance"),
        source=(None if op.get("source") in (None, "") else str(op.get("source"))),
        expires_at=expires_at,
        session=(None if op.get("session") in (None, "") else str(op.get("session"))),
        entry_id=str(entry_id),
        created_at=created_at,
    )


def _apply_forget(store: AgentMemStore, op: dict[str, Any]) -> None:
    entry_id = str(op.get("id") or "")
    reason = None if op.get("reason") in (None, "") else str(op.get("reason"))
    store.forget_ltm(entry_id, reason=reason)


def _apply_update(store: AgentMemStore, op: dict[str, Any]) -> None:
    entry_id = str(op.get("id") or "")
    patch = op.get("patch")
    if not isinstance(patch, dict) or not patch:
        raise ValueError("patch must be a non-empty table")
    reason = None if op.get("reason") in (None, "") else str(op.get("reason"))
    store.update_ltm(entry_id, patch=patch, reason=reason)


def _deterministic_add_id(op: dict[str, Any]) -> str:
    """Derive a stable id for add ops without explicit id (makes patch re-apply safe)."""
    payload = {
        "type": "add",
        "kind": op.get("kind") or "other",
        "tags": op.get("tags") or [],
        "importance": op.get("importance") or 5,
        "text": op.get("text") or "",
        "source": op.get("source") or None,
        "expires_at": op.get("expires_at") or None,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8", errors="strict")).hexdigest()
    return digest[:32]
