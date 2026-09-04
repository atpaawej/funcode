"""Sessions: append-only JSONL transcripts + index.json metadata. No sqlite.

Layout:
    ~/.config/funcode/projects/<sanitized-cwd>/
        <session-id>.jsonl      # one JSON object per line (the source of truth)
        index.json              # [{id, title, model, cwd, updated, turns}] (picker cache)

The recorder is a dumb EventBus subscriber: logging must never kill the loop,
so every write is best-effort. The loader replays a transcript back into
provider messages and skips entry types it doesn't understand (forward-compat).
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import string
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .types import Message, ToolCall

INDEX_NAME = "index.json"
RETENTION_DAYS = 30
MAX_STORED_CHARS = 20_000
TITLE_LEN = 50


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_session_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{stamp}_{rand}"


def sanitize_cwd(cwd: Path) -> str:
    name = "".join(c if c.isalnum() else "-" for c in str(cwd)).strip("-") or "root"
    if len(name) > 200:
        name = name[:190] + "-" + hashlib.sha1(str(cwd).encode()).hexdigest()[:8]
    return name


def config_root() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(xdg) / "funcode"


def project_dir(cwd: Path) -> Path:
    return config_root() / "projects" / sanitize_cwd(cwd)


def make_title(first_user_text: str) -> str:
    one_line = " ".join(first_user_text.split())
    if len(one_line) <= TITLE_LEN:
        return one_line or "untitled"
    return one_line[:TITLE_LEN].rstrip() + "…"


def _cap(s: str) -> str:
    return s if len(s) <= MAX_STORED_CHARS else s[:MAX_STORED_CHARS] + "\n...[truncated in transcript]"


class SessionRecorder:
    """Bus subscriber. File is materialized on the first user message (no orphans)."""

    def __init__(self, path: Path, session_id: str):
        self.path = path
        self.session_id = session_id
        self._materialized = path.is_file() and path.stat().st_size > 0
        self._header: dict | None = None
        self._titled = self._materialized

    @property
    def materialized(self) -> bool:
        return self._materialized

    def on_session_start(self, payload: dict) -> None:
        self._header = {"t": _now_iso(), "type": "session_start", "id": self.session_id,
                        "model": payload.get("model", ""), "cwd": payload.get("cwd", "")}

    def on_message(self, payload: dict) -> None:
        role = payload.get("role", "")
        if role == "user" and not self._materialized:
            self._write_header()
            self._materialized = True
        if not self._materialized:
            return  # don't create files for sessions with no user message
        entry: dict[str, Any] = {"t": _now_iso(), "type": role}
        if role == "user":
            entry["content"] = payload.get("content", "")
            self._write(entry)
            if not self._titled:
                self._titled = True  # title set by store from first message
        elif role == "assistant":
            entry["content"] = payload.get("content", "")
            entry["reasoning"] = _cap(payload.get("reasoning", "") or "")
            entry["tool_calls"] = payload.get("tool_calls", [])
            self._write(entry)
        elif role == "tool":
            entry["name"] = payload.get("name", "")
            entry["args"] = payload.get("args", {})
            entry["result"] = _cap(payload.get("result", ""))
            entry["tool_call_id"] = payload.get("tool_call_id", "")
            self._write(entry)

    def close(self, reason: str = "end") -> None:
        if self._materialized:
            self._write({"t": _now_iso(), "type": "session_end", "reason": reason})

    def _write_header(self) -> None:
        if self._header is not None:
            self._write(self._header)

    def _write(self, entry: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass  # logging must never break the agent


class SessionStore:
    """Paths, index.json, list/find/load, retention. One per project dir."""

    def __init__(self, cwd: Path):
        self.cwd = cwd
        self.dir = project_dir(cwd)
        self._index: list[dict] | None = None

    # -- paths --
    def path_for(self, session_id: str) -> Path:
        return self.dir / f"{session_id}.jsonl"

    # -- index --
    def _read_index(self) -> list[dict]:
        p = self.dir / INDEX_NAME
        if p.is_file():
            try:
                data = json.loads(p.read_text())
                return data if isinstance(data, list) else []
            except (OSError, json.JSONDecodeError):
                return []
        return []

    def _save_index(self, items: list[dict]) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / INDEX_NAME).write_text(json.dumps(items, indent=2))
        except OSError:
            pass

    def list_sessions(self) -> list[dict]:
        """Recent-first, titles for humans. Syncs index against files on disk,
        pruning entries whose transcript is gone (lazy sessions never materialize)."""
        items = {d["id"]: d for d in self._read_index() if isinstance(d, dict) and "id" in d}
        if self.dir.is_dir():
            alive = {f.stem for f in self.dir.glob("*.jsonl")}
            items = {sid: d for sid, d in items.items() if sid in alive}
            for f in self.dir.glob("*.jsonl"):
                sid = f.stem
                if sid not in items:
                    items[sid] = {"id": sid, "title": self._title_from_file(f),
                                  "model": "", "cwd": str(self.cwd),
                                  "updated": f.stat().st_mtime, "turns": self._count_turns(f)}
        ranked = sorted(items.values(), key=lambda d: d.get("updated", 0), reverse=True)
        self._save_index(ranked)
        return ranked

    def upsert(self, session_id: str, title: str, model: str, turns: int) -> None:
        items = [d for d in self._read_index() if d.get("id") != session_id]
        items.append({"id": session_id, "title": title, "model": model,
                      "cwd": str(self.cwd), "updated": time.time(), "turns": turns})
        self._save_index(sorted(items, key=lambda d: d.get("updated", 0), reverse=True))

    def rename(self, session_id: str, title: str) -> bool:
        items = self._read_index()
        for d in items:
            if d.get("id") == session_id:
                d["title"] = title
                self._save_index(items)
                return True
        return False

    def find(self, query: str) -> dict | None:
        """Match by id, id-prefix, or title substring (case-insensitive)."""
        q = query.lower()
        cands = self.list_sessions()
        for d in cands:
            if d["id"].lower() == q:
                return d
        prefixed = [d for d in cands if d["id"].lower().startswith(q)]
        if len(prefixed) == 1:
            return prefixed[0]
        titled = [d for d in cands if q in d.get("title", "").lower()]
        if len(titled) == 1:
            return titled[0]
        return prefixed[0] if prefixed else (titled[0] if titled else None)

    def most_recent(self) -> dict | None:
        cands = self.list_sessions()
        return cands[0] if cands else None

    # -- load / replay --
    def load_messages(self, session_id: str) -> tuple[list[Message], dict]:
        """Replay transcript -> provider messages. Skips unknown/corrupt lines."""
        msgs: list[Message] = []
        meta: dict = {"id": session_id, "title": "", "turns": 0,
                      "model": "", "cwd": "", "started": ""}
        p = self.path_for(session_id)
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            return [], meta
        for line in lines:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = e.get("type")
            if t == "session_start":
                meta["model"] = e.get("model", "")
                meta["cwd"] = e.get("cwd", "")
                meta["started"] = e.get("t", "")
            elif t == "user" and isinstance(e.get("content"), str):
                msgs.append(Message(role="user", content=e["content"]))
                meta["turns"] += 1
                if not meta["title"]:
                    meta["title"] = make_title(e["content"])
            elif t == "assistant":
                calls = [ToolCall(id=c.get("id", ""), name=c.get("name", ""),
                                  arguments=c.get("arguments", {}) if isinstance(c.get("arguments"), dict) else {})
                         for c in e.get("tool_calls", []) if isinstance(c, dict)]
                msgs.append(Message(role="assistant", content=e.get("content", "") or "",
                                    tool_calls=calls))
            elif t == "tool":
                msgs.append(Message(role="tool", content=e.get("result", "") or "",
                                    tool_call_id=e.get("tool_call_id", "") or "",
                                    tool_name=e.get("name", "") or ""))
            # session_start/session_end/summary/unknown -> skipped by design
        # Drop a trailing assistant-with-unresolved-tool-calls (interrupted turn):
        while (msgs and msgs[-1].role == "assistant" and msgs[-1].tool_calls
               and not any(m.role == "tool" for m in msgs)):
            msgs.pop()
        return msgs, meta

    # -- retention --
    def cleanup(self, days: int = RETENTION_DAYS) -> int:
        """Delete transcripts older than `days`. Returns count removed."""
        cutoff = time.time() - days * 86400
        removed = 0
        try:
            if not self.dir.is_dir():
                return 0
            for f in self.dir.glob("*.jsonl"):
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                        removed += 1
                except OSError:
                    continue
            if removed:
                alive = {f.stem for f in self.dir.glob("*.jsonl")}
                self._save_index([d for d in self._read_index() if d.get("id") in alive])
        except OSError:
            pass
        return removed

    # -- helpers --
    def _title_from_file(self, f: Path) -> str:
        try:
            with open(f, encoding="utf-8") as fh:
                for _ in range(50):
                    line = fh.readline()
                    if not line:
                        break
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if e.get("type") == "user" and e.get("content"):
                        return make_title(e["content"])
        except OSError:
            pass
        return f.stem

    @staticmethod
    def _count_turns(f: Path) -> int:
        n = 0
        try:
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        if json.loads(line).get("type") == "user":
                            n += 1
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass
        return n
