from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

MemoryKind = Literal["fact", "preference", "instruction", "profile", "note", "task", "other"]


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    id: str
    created_at: datetime
    updated_at: datetime | None
    kind: MemoryKind
    tags: tuple[str, ...] = field(default_factory=tuple)
    importance: int = 5
    text: str = ""
    source: str | None = None
    expires_at: datetime | None = None
    session: str | None = None
    forgotten_at: datetime | None = None
    forget_reason: str | None = None

    def is_active_at(self, at: datetime) -> bool:
        return not (
            (self.forgotten_at is not None and self.forgotten_at <= at)
            or (self.expires_at is not None and self.expires_at <= at)
        )

    def to_dict(self) -> dict[str, Any]:
        def dt(v: datetime | None) -> str | None:
            return v.isoformat(timespec="seconds") if v is not None else None

        return {
            "id": self.id,
            "created_at": dt(self.created_at),
            "updated_at": dt(self.updated_at),
            "kind": self.kind,
            "tags": list(self.tags),
            "importance": self.importance,
            "text": self.text,
            "source": self.source,
            "expires_at": dt(self.expires_at),
            "session": self.session,
            "forgotten_at": dt(self.forgotten_at),
            "forget_reason": self.forget_reason,
        }

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> MemoryEntry:
        def parse(v: Any) -> datetime | None:
            if v is None:
                return None
            return datetime.fromisoformat(str(v))

        tags_raw = obj.get("tags") or []
        tags: tuple[str, ...] = tuple(str(t) for t in tags_raw)

        return cls(
            id=str(obj["id"]),
            created_at=datetime.fromisoformat(str(obj["created_at"])),
            updated_at=parse(obj.get("updated_at")),
            kind=str(obj.get("kind") or "other"),  # type: ignore[arg-type]
            tags=tags,
            importance=int(obj.get("importance", 5)),
            text=str(obj.get("text") or ""),
            source=(None if obj.get("source") in (None, "") else str(obj["source"])),
            expires_at=parse(obj.get("expires_at")),
            session=(None if obj.get("session") in (None, "") else str(obj["session"])),
            forgotten_at=parse(obj.get("forgotten_at")),
            forget_reason=(
                None
                if obj.get("forget_reason") in (None, "")
                else str(obj["forget_reason"])
            ),
        )


@dataclass(frozen=True, slots=True)
class SessionMessage:
    ts: datetime
    role: Literal["system", "user", "assistant"]
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": "msg",
            "ts": self.ts.isoformat(timespec="seconds"),
            "role": self.role,
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> SessionMessage:
        return cls(
            ts=datetime.fromisoformat(str(obj["ts"])),
            role=str(obj["role"]),  # type: ignore[arg-type]
            text=str(obj.get("text") or ""),
        )
