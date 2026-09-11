"""Slash-command abstractions. Mirrors core/tools.py on purpose.

- Command: single slash unit (PROMPT = markdown template, ACTION = Python callback)
- CommandProvider: a source of commands (builtin, markdown files, skills, plugins)
- CommandRegistry: aggregates providers, expands templates, dispatches
- Expansion: $ARGUMENTS / $1..$N / !`shell` / @file (OpenCode parity)

Loop never imports markdown here; providers implement the ABC.
"""
from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

CommandKind = Literal["prompt", "action"]

_TEMPLATE_TRUNC = 20_000
_AT_READ_CAP = 8_000
_SHELL_TIMEOUT = 10


@dataclass
class CommandContext:
    """What an ACTION command may touch. `app` is opaque (main.App)."""

    cwd: Path
    raw_args: str = ""
    app: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Command:
    name: str
    description: str = ""
    kind: CommandKind = "prompt"
    source: str = "builtin"
    path: Path | None = None
    template: str = ""
    action: Callable[[CommandContext], str | None] | None = None

    @property
    def is_action(self) -> bool:
        return self.kind == "action"


class CommandProvider:
    """A source of commands. Builtin/markdown/skills/plugins all implement this."""

    name: str = "unknown"

    def list_commands(self) -> list[Command]:
        return []

    def reload(self) -> None:
        """Optional: re-scan disk. No-op by default."""


def split_command(text: str) -> tuple[str, str]:
    """Split `/name args...` into (name, raw_args). Leading slash optional."""
    t = text.strip()
    if t.startswith("/"):
        t = t[1:]
    if not t:
        return "", ""
    parts = t.split(None, 1)
    return parts[0].lower(), (parts[1] if len(parts) > 1 else "")


def _positional_argv(raw_args: str) -> list[str]:
    try:
        return shlex.split(raw_args)
    except ValueError:
        return raw_args.split()


def expand_template(template: str, raw_args: str, cwd: Path) -> str:
    """Expand $ARGUMENTS/$1..$N, !`shell`, @file. Never raises."""
    try:
        out = _expand_shell(template, cwd)
        out = _expand_files(out, cwd)
        out = _expand_args(out, raw_args)
        return out[:_TEMPLATE_TRUNC]
    except Exception as e:
        return f"{template}\n\n[command expansion warning: {e}]"


def _expand_args(template: str, raw_args: str) -> str:
    argv = _positional_argv(raw_args)

    def _pos(m: re.Match) -> str:
        i = int(m.group(1)) - 1
        return argv[i] if 0 <= i < len(argv) else ""

    out = re.sub(r"\$([1-9])", _pos, template)
    return out.replace("$ARGUMENTS", raw_args)


def _expand_shell(template: str, cwd: Path) -> str:
    def _run(m: re.Match) -> str:
        cmd = m.group(1).strip()
        if not cmd:
            return ""
        try:
            r = subprocess.run(cmd, shell=True, cwd=str(cwd), capture_output=True,
                               text=True, timeout=_SHELL_TIMEOUT)
            out = (r.stdout or "") + (f"\n[stderr]\n{r.stderr}" if r.stderr else "")
            out = out.strip() or "(no output)"
            return out[:4000]
        except subprocess.TimeoutExpired:
            return f"(shell timed out: {cmd})"
        except Exception as e:
            return f"(shell error: {e})"

    return re.sub(r"!`([^`]+)`", _run, template)


def _expand_files(template: str, cwd: Path) -> str:
    def _read(m: re.Match) -> str:
        rel = m.group(1).strip().strip("\"'")
        if not rel:
            return m.group(0)
        p = (cwd / rel).resolve()
        try:
            root = cwd.resolve()
            if root not in p.parents and p != root:
                return f"(file outside workspace, skipped: {rel})"
        except Exception:
            return f"(file not found: {rel})"
        if not p.is_file():
            return f"(file not found: {rel})"
        try:
            if p.stat().st_size > 200_000:
                return f"(file too large, skipped: {rel})"
            text = p.read_text(errors="replace")[:_AT_READ_CAP]
            return f"--- {rel} ---\n{text}"
        except OSError as e:
            return f"(file error {rel}: {e})"

    # @path with word chars, dots, dashes, slashes. Trailing punctuation excluded.
    return re.sub(r"@([\w.\-+/]+(?:/[\w.\-+/]+)*)", _read, template)


class CommandRegistry:
    """Aggregates providers. Later providers override same names (project > global > builtin)."""

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}
        self._providers: dict[str, CommandProvider] = {}

    def add_provider(self, provider: CommandProvider) -> None:
        self._providers[provider.name] = provider
        for c in provider.list_commands():
            self.register(c)

    def register(self, cmd: Command) -> None:
        self._commands[cmd.name.lower()] = cmd

    def get(self, name: str) -> Command | None:
        key = name.lstrip("/").lower()
        return self._commands.get(key)

    def names(self) -> list[str]:
        return sorted(self._commands)

    def list_all(self) -> list[Command]:
        return [self._commands[k] for k in sorted(self._commands)]

    def grouped(self) -> dict[str, list[Command]]:
        groups: dict[str, list[Command]] = {}
        for cmd in self.list_all():
            groups.setdefault(cmd.source, []).append(cmd)
        return groups

    def reload(self) -> None:
        for p in self._providers.values():
            try:
                p.reload()
            except Exception:
                continue
        self._commands.clear()
        for p in self._providers.values():
            for c in p.list_commands():
                self.register(c)

    def expand(self, cmd: Command, raw_args: str, cwd: Path) -> str:
        return expand_template(cmd.template, raw_args, cwd)
