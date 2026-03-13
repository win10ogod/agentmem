from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from . import __version__
from .search import bm25_search
from .store import AgentMemError, AgentMemStore
from .utils import default_home, is_tty


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    ns = parser.parse_args(argv)

    try:
        return _dispatch(ns)
    except KeyboardInterrupt:
        if is_tty(sys.stderr):
            print("Interrupted.", file=sys.stderr)
        return 130
    except AgentMemError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agentmem",
        description="Pure-text CLI memory store for AI agents.",
    )
    p.add_argument("--version", action="version", version=f"agentmem {__version__}")
    p.add_argument(
        "--home",
        type=Path,
        default=None,
        help="Memory home directory (default: auto-detect)",
    )

    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="Initialize memory home layout")

    add = sub.add_parser("add", help="Add a long-term memory entry (LTM)")
    add.add_argument("--kind", default="other")
    add.add_argument("--tags", default="")
    add.add_argument("--importance", type=int, default=5)
    add.add_argument("--source", default=None)
    add.add_argument(
        "--expires-at",
        default=None,
        help="ISO datetime with offset, e.g. 2026-03-13T00:00:00+00:00",
    )
    add.add_argument("--id", dest="entry_id", default=None, help="Optional explicit entry id")
    add.add_argument("--text", default=None, help="Entry text. If omitted, read from stdin.")

    upd = sub.add_parser("update", help="Update an existing long-term memory entry (LTM)")
    upd.add_argument("id")
    upd.add_argument("--kind", default=None)
    upd.add_argument("--tags", default=None, help='Comma-separated. Use "" to clear.')
    upd.add_argument("--importance", type=int, default=None)
    upd.add_argument("--source", default=None)
    upd.add_argument(
        "--expires-at",
        default=None,
        help="ISO datetime with offset, e.g. 2026-03-13T00:00:00+00:00",
    )
    upd.add_argument(
        "--text",
        default=None,
        help="New text.",
    )
    upd.add_argument(
        "--stdin",
        action="store_true",
        help="Read new text from stdin (non-interactive).",
    )
    upd.add_argument("--reason", default=None)

    recall = sub.add_parser("recall", help="Search long-term memory (LTM)")
    recall.add_argument("query")
    recall.add_argument("--limit", type=int, default=10)
    recall.add_argument(
        "--include-inactive",
        action="store_true",
        help="Include forgotten/expired entries",
    )
    recall.add_argument(
        "--as-of",
        default=None,
        help="Time-travel: ISO datetime (UTC offset required)",
    )
    recall.add_argument("--kind", default=None, help="Filter by kind")
    recall.add_argument("--tag", action="append", default=[], help="Filter by tag (repeatable)")
    recall.add_argument("--format", choices=["text", "json", "md"], default="text")
    recall.add_argument(
        "--explain",
        action="store_true",
        help="Explain ranking (matched terms, scores)",
    )

    ls = sub.add_parser("list", help="List long-term memory entries (LTM)")
    ls.add_argument("--limit", type=int, default=50)
    ls.add_argument("--include-inactive", action="store_true")
    ls.add_argument("--format", choices=["text", "json"], default="text")

    fg = sub.add_parser("forget", help="Forget (tombstone) a long-term memory entry by id")
    fg.add_argument("id")
    fg.add_argument("--reason", default=None)

    session = sub.add_parser("session", help="Short-term memory (STM) sessions")
    ssub = session.add_subparsers(dest="session_cmd", required=True)

    ssub.add_parser("start", help="Start a new session")

    sadd = ssub.add_parser("add", help="Add a message to a session")
    sadd.add_argument("--session", required=True)
    sadd.add_argument("--role", choices=["system", "user", "assistant"], required=True)
    sadd.add_argument("--text", default=None, help="Message text. If omitted, read from stdin.")

    sshow = ssub.add_parser("show", help="Show a session transcript")
    sshow.add_argument("--session", required=True)
    sshow.add_argument("--limit", type=int, default=200)
    sshow.add_argument("--format", choices=["text", "json"], default="text")

    srecall = ssub.add_parser("recall", help="Search within a session transcript")
    srecall.add_argument("--session", required=True)
    srecall.add_argument("query")
    srecall.add_argument("--limit", type=int, default=10)
    srecall.add_argument("--format", choices=["text", "json"], default="text")
    srecall.add_argument("--explain", action="store_true")

    scommit = ssub.add_parser("commit", help="Promote durable items from STM session into LTM")
    scommit.add_argument("--session", required=True)
    scommit.add_argument(
        "--auto",
        action="store_true",
        help="Auto-extract durable memories (offline rules)",
    )
    scommit.add_argument("--format", choices=["text", "json"], default="text")

    patch = sub.add_parser("patch", help="MemoryPatch (TOML) workflows")
    psub = patch.add_subparsers(dest="patch_cmd", required=True)
    psub.add_parser("template", help="Print a patch template (TOML)")

    pval = psub.add_parser("validate", help="Validate a patch file")
    pval.add_argument("path", type=Path)

    pap = psub.add_parser("apply", help="Apply a patch file to LTM")
    pap.add_argument("path", type=Path)
    pap.add_argument("--dry-run", action="store_true")

    comp = sub.add_parser("completion", help="Print shell completion script")
    comp.add_argument("shell", choices=["bash", "zsh", "fish"])

    return p


def _dispatch(ns: argparse.Namespace) -> int:
    home = ns.home or default_home()
    store = AgentMemStore(home)

    match ns.cmd:
        case "init":
            store.init_layout()
            print(str(store.paths.home))
            return 0
        case "add":
            text = _read_text_arg_or_stdin(ns.text)
            entry_id = store.add_ltm(
                text=text,
                kind=ns.kind,
                tags=ns.tags,
                importance=ns.importance,
                source=ns.source,
                expires_at=ns.expires_at,
                entry_id=ns.entry_id,
            )
            print(entry_id)
            return 0
        case "recall":
            as_of = _parse_as_of(ns.as_of)
            entries = store.load_ltm(as_of=as_of, include_inactive=ns.include_inactive)
            entries = _filter_entries(entries, kind=ns.kind, tags=ns.tag)
            hits = bm25_search(entries, ns.query, limit=ns.limit)
            _print_hits(hits, fmt=ns.format, explain=ns.explain)
            return 0
        case "update":
            patch: dict[str, Any] = {}
            if ns.kind is not None:
                patch["kind"] = ns.kind
            if ns.tags is not None:
                patch["tags"] = ns.tags
            if ns.importance is not None:
                patch["importance"] = ns.importance
            if ns.source is not None:
                patch["source"] = ns.source
            if ns.expires_at is not None:
                patch["expires_at"] = ns.expires_at
            if ns.text is not None:
                patch["text"] = ns.text
            elif ns.stdin:
                patch["text"] = sys.stdin.read()

            store.update_ltm(ns.id, patch=patch, reason=ns.reason)
            return 0
        case "list":
            entries = store.load_ltm(include_inactive=ns.include_inactive)
            entries = entries[: max(0, ns.limit)]
            _print_entries(entries, fmt=ns.format)
            return 0
        case "forget":
            store.forget_ltm(ns.id, reason=ns.reason)
            return 0
        case "session":
            return _dispatch_session(store, ns)
        case "patch":
            return _dispatch_patch(store, ns)
        case "completion":
            print(_completion_script(ns.shell))
            return 0
        case _:
            raise AgentMemError(f"unknown command: {ns.cmd}")


def _dispatch_session(store: AgentMemStore, ns: argparse.Namespace) -> int:
    match ns.session_cmd:
        case "start":
            sid = store.start_session()
            print(sid)
            return 0
        case "add":
            text = _read_text_arg_or_stdin(ns.text)
            store.add_session_message(ns.session, role=ns.role, text=text)
            return 0
        case "show":
            msgs = store.load_session(ns.session)
            msgs = msgs[-max(0, ns.limit) :]
            if ns.format == "json":
                print(
                    json.dumps(
                        [m.to_dict() for m in msgs], ensure_ascii=False, separators=(",", ":")
                    )
                )
                return 0
            for m in msgs:
                ts = m.ts.isoformat(timespec="seconds")
                print(f"[{ts}] {m.role}: {m.text}")
            return 0
        case "recall":
            msgs = store.load_session(ns.session)
            pseudo_entries = _messages_as_entries(ns.session, msgs)
            hits = bm25_search(pseudo_entries, ns.query, limit=ns.limit)
            if ns.format == "json":
                print(
                    json.dumps(
                        [
                            {
                                "score": h.score,
                                "matched_terms": h.matched_terms,
                                "entry": h.entry.to_dict(),
                            }
                            for h in hits
                        ],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                )
                return 0
            _print_hits(hits, fmt="text", explain=ns.explain)
            return 0
        case "commit":
            if not ns.auto:
                raise AgentMemError("only --auto commit is implemented (use --auto)")
            added = store.commit_session_auto(ns.session)
            if ns.format == "json":
                print(json.dumps({"added": added}, ensure_ascii=False, separators=(",", ":")))
                return 0
            for mid in added:
                print(mid)
            return 0
        case _:
            raise AgentMemError(f"unknown session command: {ns.session_cmd}")


def _dispatch_patch(store: AgentMemStore, ns: argparse.Namespace) -> int:
    from .patch import apply_patch, load_patch_toml, validate_patch

    match ns.patch_cmd:
        case "template":
            print(_patch_template())
            return 0
        case "validate":
            patch = load_patch_toml(ns.path)
            summary = validate_patch(patch)
            _print_patch_summary(summary)
            return 0
        case "apply":
            patch = load_patch_toml(ns.path)
            summary = validate_patch(patch)
            _print_patch_summary(summary)
            if ns.dry_run:
                return 0
            applied = apply_patch(store, patch)
            if applied.errors:
                raise AgentMemError("; ".join(applied.errors))
            return 0
        case _:
            raise AgentMemError(f"unknown patch command: {ns.patch_cmd}")


def _messages_as_entries(session: str, msgs: list[Any]) -> list[Any]:
    from .model import MemoryEntry

    out: list[MemoryEntry] = []
    for idx, m in enumerate(msgs):
        out.append(
            MemoryEntry(
                id=f"{session}:{idx}",
                created_at=m.ts,
                updated_at=None,
                kind="note",
                tags=("stm", m.role),
                importance=5,
                text=m.text,
                source=f"session:{session}",
                expires_at=None,
                session=session,
                forgotten_at=None,
                forget_reason=None,
            )
        )
    return out


def _filter_entries(entries: list[Any], *, kind: str | None, tags: list[str]) -> list[Any]:
    k = (kind or "").strip().lower()
    tset = {t.strip() for t in tags if t.strip()}
    out = []
    for e in entries:
        if k and str(getattr(e, "kind", "")).lower() != k:
            continue
        if tset:
            etags = set(getattr(e, "tags", ()))
            if not (tset & etags):
                continue
        out.append(e)
    return out


def _read_text_arg_or_stdin(text: str | None) -> str:
    if text is not None:
        return text
    if not is_tty(sys.stdin):
        return sys.stdin.read()
    print("Enter text, end with EOF (Ctrl-D).", file=sys.stderr)
    return sys.stdin.read()


def _parse_as_of(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as e:
        raise AgentMemError(f"invalid --as-of datetime: {value}") from e


def _print_entries(entries: list[Any], *, fmt: Literal["text", "json"]) -> None:
    if fmt == "json":
        print(json.dumps([e.to_dict() for e in entries], ensure_ascii=False, separators=(",", ":")))
        return
    for e in entries:
        tags = ",".join(e.tags)
        created = e.created_at.isoformat(timespec="seconds")
        status = "active" if (e.forgotten_at is None) else "forgotten"
        print(f"{e.id}\t{created}\t{status}\t{e.kind}\t{tags}\t{e.importance}\t{e.text}")


def _print_hits(hits: list[Any], *, fmt: Literal["text", "json", "md"], explain: bool) -> None:
    if fmt == "json":
        print(
            json.dumps(
                [
                    {
                        "score": h.score,
                        "matched_terms": h.matched_terms,
                        "term_counts": h.term_counts,
                        "entry": h.entry.to_dict(),
                    }
                    for h in hits
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return

    if fmt == "md":
        for h in hits:
            e = h.entry
            tags = ", ".join(e.tags)
            created = e.created_at.isoformat(timespec="seconds")
            meta = (
                f"{e.kind}, importance={e.importance}, created_at={created}, tags=[{tags}]"
            )
            print(f"- **{e.id}** ({meta})")
            if explain:
                print(f"  - score={h.score:.4f}, matched={list(h.matched_terms)}")
            for line in e.text.strip().splitlines():
                print(f"  - {line}")
        return

    for h in hits:
        e = h.entry
        tags = ",".join(e.tags)
        created = e.created_at.isoformat(timespec="seconds")
        header = (
            f"[{e.id}] kind={e.kind} importance={e.importance} "
            f"created_at={created} tags={tags}"
        )
        if explain:
            header += f" score={h.score:.4f} matched={list(h.matched_terms)}"
        print(header)
        print(e.text.rstrip())
        print()


def _patch_template() -> str:
    return """format = "agentmem-patch"
version = 1

[[op]]
type = "add"
kind = "preference"
tags = ["user", "preference"]
importance = 7
text = \"\"\"之後請都用繁體中文回覆。\"\"\"

[[op]]
type = "forget"
id = "PUT_ENTRY_ID_HERE"
reason = "outdated"
"""


def _print_patch_summary(summary: Any) -> None:
    # summary is a small dataclass; keep output stable for piping.
    print(json.dumps(asdict(summary), ensure_ascii=False, separators=(",", ":")))


def _completion_script(shell: Literal["bash", "zsh", "fish"]) -> str:
    # Minimal static completions; fast and dependency-free.
    cmds = [
        "init",
        "add",
        "update",
        "recall",
        "list",
        "forget",
        "session",
        "patch",
        "completion",
    ]
    session_cmds = ["start", "add", "show", "recall", "commit"]
    patch_cmds = ["template", "validate", "apply"]

    if shell == "fish":
        lines = ["# agentmem fish completion"]
        for c in cmds:
            lines.append(f"complete -c agentmem -f -n '__fish_use_subcommand' -a {c}")
        for c in session_cmds:
            lines.append(
                "complete -c agentmem -f -n '__fish_seen_subcommand_from session' -a " + c
            )
        for c in patch_cmds:
            lines.append(
                "complete -c agentmem -f -n '__fish_seen_subcommand_from patch' -a " + c
            )
        return "\n".join(lines) + "\n"

    if shell == "zsh":
        return (
            "#compdef agentmem\n"
            "_agentmem() {\n"
            "  local -a commands\n"
            f"  commands=({' '.join(cmds)})\n"
            "  _describe 'command' commands\n"
            "}\n"
            "compdef _agentmem agentmem\n"
        )

    # bash
    words = " ".join(cmds)
    return (
        "# agentmem bash completion\n"
        "_agentmem_complete() {\n"
        "  local cur prev\n"
        "  cur=\"${COMP_WORDS[COMP_CWORD]}\"\n"
        "  prev=\"${COMP_WORDS[COMP_CWORD-1]}\"\n"
        "  if [[ ${COMP_CWORD} -eq 1 ]]; then\n"
        f"    COMPREPLY=( $(compgen -W \"{words}\" -- \"$cur\") )\n"
        "    return 0\n"
        "  fi\n"
        "}\n"
        "complete -F _agentmem_complete agentmem\n"
    )
