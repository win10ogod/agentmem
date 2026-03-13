from __future__ import annotations

import contextlib
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    """Return current time as ISO-8601 with UTC offset."""
    # Use microseconds to keep event ordering stable for time-travel queries.
    return datetime.now(UTC).isoformat(timespec="microseconds")


def parse_iso_datetime(value: str) -> datetime:
    """Parse an ISO-8601 datetime string produced by this project."""
    return datetime.fromisoformat(value)


def is_tty(stream: Any) -> bool:
    """Best-effort TTY detection for stdout/stderr."""
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read newline-delimited JSON objects from a UTF-8 text file."""
    if not path.exists():
        return []
    items: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            items.append(json.loads(line))
    return items


def append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    """Append a JSON object as a single line to a UTF-8 text file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(line)
        f.write("\n")


def write_json(path: Path, obj: dict[str, Any]) -> None:
    """Write a JSON object to a UTF-8 text file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def read_json(path: Path) -> dict[str, Any] | None:
    """Read a JSON object from a UTF-8 text file."""
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        obj = json.load(f)
        return obj if isinstance(obj, dict) else None


@dataclass(frozen=True)
class FileFingerprint:
    """A minimal, text-serializable fingerprint for cache validation."""

    size: int
    mtime_ns: int

    @classmethod
    def from_path(cls, path: Path) -> FileFingerprint:
        st = path.stat()
        return cls(size=st.st_size, mtime_ns=st.st_mtime_ns)

    def to_json(self) -> dict[str, Any]:
        return {"size": self.size, "mtime_ns": self.mtime_ns}

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> FileFingerprint:
        return cls(size=int(obj["size"]), mtime_ns=int(obj["mtime_ns"]))


class FileLockTimeout(RuntimeError):
    pass


class FileLock:
    """Cross-platform lock via atomic lock file creation.

    This is not a perfect distributed lock, but works well for a local CLI tool.
    The lock file is plain text and includes pid + created_at for debugging.
    """

    def __init__(
        self,
        lock_path: Path,
        *,
        timeout_s: float = 10.0,
        poll_s: float = 0.05,
        stale_s: float = 120.0,
    ) -> None:
        self._lock_path = lock_path
        self._timeout_s = timeout_s
        self._poll_s = poll_s
        self._stale_s = stale_s
        self._fd: int | None = None

    def __enter__(self) -> FileLock:
        deadline = time.monotonic() + self._timeout_s
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)

        while True:
            try:
                fd = os.open(
                    str(self._lock_path),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o644,
                )
                self._fd = fd
                payload = f"pid={os.getpid()}\ncreated_at={utc_now_iso()}\n"
                os.write(fd, payload.encode("utf-8", errors="strict"))
                os.fsync(fd)
                return self
            except FileExistsError:
                if self._is_stale():
                    self._break_stale()
                    continue
                if time.monotonic() >= deadline:
                    raise FileLockTimeout(
                        f"Timed out waiting for lock: {self._lock_path}"
                    ) from None
                time.sleep(self._poll_s)

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
        with contextlib.suppress(OSError):
            self._lock_path.unlink(missing_ok=True)

    def _is_stale(self) -> bool:
        try:
            age_s = max(0.0, time.time() - self._lock_path.stat().st_mtime)
        except FileNotFoundError:
            return False
        return age_s >= self._stale_s

    def _break_stale(self) -> None:
        with contextlib.suppress(OSError):
            self._lock_path.unlink(missing_ok=True)


def default_home() -> Path:
    """Resolve the default agentmem home directory.

    Priority:
    1) $AGENTMEM_HOME
    2) ./.agentmem (if exists)
    3) ~/.agentmem
    """

    env = os.environ.get("AGENTMEM_HOME")
    if env:
        return Path(env).expanduser()

    cwd_home = Path.cwd() / ".agentmem"
    if cwd_home.exists() and cwd_home.is_dir():
        return cwd_home

    return Path.home() / ".agentmem"
