"""Builtin tools: read, write, edit, bash, glob, grep."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from ..core.tools import Tool, ToolContext, ToolProvider

_TRUNC = 20_000


def _resolve(cwd: Path, p: str) -> Path:
    path = Path(p)
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()


def _truncate(s: str) -> str:
    return s if len(s) <= _TRUNC else s[:_TRUNC] + f"\n...[truncated {len(s) - _TRUNC} chars]"


class ReadTool(Tool):
    name = "read"
    description = "Read a file. Returns numbered lines."
    risk = "read"
    parameters = {"type": "object", "properties": {
        "path": {"type": "string"},
        "offset": {"type": "integer", "default": 1},
        "limit": {"type": "integer", "default": 200},
    }, "required": ["path"]}

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        p = _resolve(ctx.cwd, args["path"])
        if not p.is_file():
            return f"Error: not a file: {args['path']}"
        lines = p.read_text(errors="replace").splitlines()
        off = max(1, int(args.get("offset", 1))) - 1
        lim = int(args.get("limit", 200))
        chunk = lines[off:off + lim]
        numbered = "\n".join(f"{off + i + 1:6d}  {l}" for i, l in enumerate(chunk))
        return _truncate(numbered or "(empty file)")


class WriteTool(Tool):
    name = "write"
    description = "Create or overwrite a file with full content."
    risk = "write"
    parameters = {"type": "object", "properties": {
        "path": {"type": "string"}, "content": {"type": "string"},
    }, "required": ["path", "content"]}

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        p = _resolve(ctx.cwd, args["path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args.get("content", ""))
        return f"Wrote {len(args.get('content', ''))} chars to {args['path']}"


class EditTool(Tool):
    name = "edit"
    description = "Exact string replace in a file. old_string must match once."
    risk = "write"
    parameters = {"type": "object", "properties": {
        "path": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"},
    }, "required": ["path", "old_string", "new_string"]}

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        p = _resolve(ctx.cwd, args["path"])
        if not p.is_file():
            return f"Error: not a file: {args['path']}"
        text = p.read_text(errors="replace")
        old, new = args["old_string"], args["new_string"]
        n = text.count(old)
        if n == 0:
            return "Error: old_string not found"
        if n > 1:
            return f"Error: old_string matches {n} times, be more specific"
        p.write_text(text.replace(old, new))
        return f"Edited {args['path']}"


class BashTool(Tool):
    name = "bash"
    description = "Run a shell command in cwd. Returns stdout+stderr."
    risk = "dangerous"
    parameters = {"type": "object", "properties": {
        "command": {"type": "string"},
        "timeout": {"type": "integer", "default": 30},
    }, "required": ["command"]}

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        try:
            r = subprocess.run(args["command"], shell=True, cwd=str(ctx.cwd),
                               capture_output=True, text=True,
                               timeout=int(args.get("timeout", 30)))
            out = (r.stdout or "") + (f"\n[stderr]\n{r.stderr}" if r.stderr else "")
            return _truncate(f"$ {args['command']}\n(exit {r.returncode})\n{out}".strip() or "(no output)")
        except subprocess.TimeoutExpired:
            return "Error: command timed out"


class GlobTool(Tool):
    name = "glob"
    description = "Find files by glob pattern, e.g. '**/*.py'."
    risk = "read"
    parameters = {"type": "object", "properties": {
        "pattern": {"type": "string"}, "limit": {"type": "integer", "default": 50},
    }, "required": ["pattern"]}

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        hits = sorted(str(p.relative_to(ctx.cwd)) for p in ctx.cwd.glob(args["pattern"]) if p.is_file())
        lim = int(args.get("limit", 50))
        shown = hits[:lim]
        extra = f"\n... +{len(hits) - lim} more" if len(hits) > lim else ""
        return "\n".join(shown) + extra if shown else "(no matches)"


class GrepTool(Tool):
    name = "grep"
    description = "Search file contents with regex. Scans cwd recursively."
    risk = "read"
    parameters = {"type": "object", "properties": {
        "pattern": {"type": "string"}, "include": {"type": "string", "default": "*"},
        "limit": {"type": "integer", "default": 40},
    }, "required": ["pattern"]}

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        rx = re.compile(args["pattern"])
        inc = args.get("include", "*")
        lim = int(args.get("limit", 40))
        out: list[str] = []
        for p in ctx.cwd.rglob(inc):
            if not p.is_file() or p.stat().st_size > 500_000:
                continue
            if ".git" in p.parts or "__pycache__" in p.parts:
                continue
            try:
                for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
                    if rx.search(line):
                        out.append(f"{p.relative_to(ctx.cwd)}:{i}: {line[:300]}")
                        if len(out) >= lim:
                            return _truncate("\n".join(out))
            except OSError:
                continue
        return "\n".join(out) if out else "(no matches)"


class BuiltinProvider(ToolProvider):
    name = "builtin"

    def list_tools(self) -> list[Tool]:
        return [ReadTool(), WriteTool(), EditTool(), BashTool(), GlobTool(), GrepTool()]


def register_builtin(registry) -> None:
    """Back-compat helper; prefer registry.add_provider(BuiltinProvider())."""
    registry.add_provider(BuiltinProvider())
