from __future__ import annotations

import contextlib
import json
import os
import socket
import socketserver
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .batch import _handle_op
from .store import AgentMemError, AgentMemStore
from .utils import utc_now_iso, write_json


@dataclass(frozen=True, slots=True)
class DaemonState:
    format: str
    version: int
    pid: int
    started_at: str
    home: str
    host: str
    port: int
    token: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "pid": self.pid,
            "started_at": self.started_at,
            "home": self.home,
            "host": self.host,
            "port": self.port,
            "token": self.token,
        }

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> DaemonState:
        if obj.get("format") != "agentmem-daemon" or int(obj.get("version", 0)) != 1:
            raise ValueError("invalid daemon state file")
        token = obj.get("token")
        return cls(
            format="agentmem-daemon",
            version=1,
            pid=int(obj["pid"]),
            started_at=str(obj["started_at"]),
            home=str(obj["home"]),
            host=str(obj["host"]),
            port=int(obj["port"]),
            token=(None if token in (None, "") else str(token)),
        )


def default_state_path(home: Path) -> Path:
    return home / "daemon.json"


def load_state(path: Path) -> DaemonState:
    if not path.exists():
        raise AgentMemError(f"daemon state not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("daemon state file must be a JSON object")
        return DaemonState.from_dict(raw)
    except Exception as e:
        raise AgentMemError(f"invalid daemon state file: {path}") from e


def send_request(
    state: DaemonState,
    req: dict[str, Any],
    *,
    timeout_s: float = 2.0,
) -> dict[str, Any]:
    req = dict(req)
    if state.token is not None:
        req.setdefault("token", state.token)
    payload = json.dumps(req, ensure_ascii=False, separators=(",", ":")) + "\n"

    try:
        with socket.create_connection((state.host, state.port), timeout=timeout_s) as sock:
            sock.sendall(payload.encode("utf-8", errors="strict"))
            f = sock.makefile("r", encoding="utf-8", newline="\n")
            line = f.readline()
            if not line:
                raise AgentMemError("daemon closed connection without response")
            obj = json.loads(line)
            if not isinstance(obj, dict):
                raise AgentMemError("daemon response must be a JSON object")
            return obj
    except OSError as e:
        raise AgentMemError(f"failed to connect to daemon at {state.host}:{state.port}") from e


class _DaemonServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], store: AgentMemStore, token: str | None) -> None:
        super().__init__(address, _DaemonHandler)
        self.store = store
        self.token = token
        self._lock = threading.Lock()
        self._docs_cache: list[Any] | None = None
        self._docs_fp: Any = None


class _DaemonHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        server: _DaemonServer = self.server  # type: ignore[assignment]
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode("utf-8", errors="strict").strip()
            if not line:
                continue

            resp: dict[str, Any]
            try:
                obj = json.loads(line)
                if not isinstance(obj, dict):
                    raise ValueError("request must be a JSON object")
                req: dict[str, Any] = obj
            except Exception as e:
                resp = {"ok": False, "op": None, "error": str(e)}
                self._write(resp)
                continue

            token = req.get("token")
            if server.token is not None and token != server.token:
                resp = {"ok": False, "op": req.get("op"), "error": "unauthorized"}
                self._write(resp)
                continue

            op = req.get("op")
            if op == "ping":
                resp = {
                    "ok": True,
                    "op": "ping",
                    "result": {
                        "version": __version__,
                        "pid": os.getpid(),
                        "home": str(server.store.paths.home),
                    },
                }
                self._write(resp)
                continue

            if op == "shutdown":
                resp = {"ok": True, "op": "shutdown", "result": {"status": "shutting_down"}}
                self._write(resp)
                threading.Thread(target=server.shutdown, daemon=True).start()
                return

            try:
                with server._lock:
                    result, ltm_mutated, server._docs_cache, server._docs_fp = _handle_op(
                        server.store,
                        op=str(op or ""),
                        req=_strip_token(req),
                        docs_cache=server._docs_cache,
                        docs_fp=server._docs_fp,
                    )
                    if ltm_mutated:
                        server._docs_cache = None
                        server._docs_fp = None
                resp = {"ok": True, "op": op, "result": result}
            except Exception as e:
                resp = {"ok": False, "op": op, "error": str(e)}

            self._write(resp)

    def _write(self, obj: dict[str, Any]) -> None:
        payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"
        self.wfile.write(payload.encode("utf-8", errors="strict"))
        self.wfile.flush()


def _strip_token(req: dict[str, Any]) -> dict[str, Any]:
    if "token" not in req:
        return req
    out = dict(req)
    out.pop("token", None)
    return out


def serve_forever(
    *,
    store: AgentMemStore,
    host: str,
    port: int,
    token: str | None,
    state_path: Path | None,
) -> int:
    store.init_layout()

    if token == "":
        token = None
    if token is None:
        token = uuid.uuid4().hex

    server = _DaemonServer((host, port), store, token=token)
    addr = server.server_address
    actual_host = str(addr[0])
    actual_port = int(addr[1])
    state = DaemonState(
        format="agentmem-daemon",
        version=1,
        pid=os.getpid(),
        started_at=utc_now_iso(),
        home=str(store.paths.home),
        host=str(actual_host),
        port=int(actual_port),
        token=token,
    )

    if state_path is not None:
        write_json(state_path, state.to_dict())

    # Print one-line JSON state to stdout for scripting
    print(json.dumps(state.to_dict(), ensure_ascii=False, separators=(",", ":")))

    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        return 130
    finally:
        server.server_close()
        if state_path is not None:
            with contextlib.suppress(OSError):
                state_path.unlink(missing_ok=True)

    return 0
