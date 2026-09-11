"""File-based commands + skills discovery.

Roots (high → low priority, later registrations override):
  project  .funcode/commands/*.md, .funcode/skills/*/SKILL.md
  agents   .agents/commands/*.md,  .agents/skills/*/SKILL.md   (generic, shared)
  compat   .claude/commands, .claude/skills, .opencode/commands (read-only interop)
  global   ~/.config/funcode/commands/*.md, ~/.config/funcode/skills/*/SKILL.md

Filename (commands) or dirname (skills) is the command name; SKILL.md
`name:` frontmatter wins when present. Optional frontmatter:
  ---
  description: one line for /help
  name: override (skills mostly)
  ---
"""
from __future__ import annotations

import os
from pathlib import Path

from . import Command, CommandProvider


def _read_text(p: Path) -> str | None:
    try:
        return p.read_text(errors="replace")
    except OSError:
        return None


def parse_markdown_command(path: Path, default_name: str) -> Command | None:
    """Parse one .md file into a PROMPT Command. Never raises."""
    text = _read_text(path)
    if text is None:
        return None
    description, name, body = _split_frontmatter(text, default_name)
    if not body.strip():
        return None
    return Command(name=name, description=description, kind="prompt",
                   source="", template=body, path=path)


def _split_frontmatter(text: str, default_name: str) -> tuple[str, str, str]:
    name, description, body = default_name, "", text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm = text[3:end].strip().lstrip("\n")
            body = text[end + 4:].lstrip("\n")
            for line in fm.splitlines():
                line = line.strip()
                if line.lower().startswith("description:"):
                    description = line.split(":", 1)[1].strip().strip("\"'")
                elif line.lower().startswith("name:"):
                    v = line.split(":", 1)[1].strip().strip("\"'").lstrip("/")
                    if v:
                        name = v.lower()
    if not description:
        # First non-empty body line as fallback description.
        for line in body.splitlines():
            s = line.strip().lstrip("#>*- ").strip()
            if s:
                description = s[:100]
                break
    return description, name.lower(), body


def _load_commands_dir(d: Path, source: str) -> list[Command]:
    out: list[Command] = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.md")):
        if not p.is_file():
            continue
        cmd = parse_markdown_command(p, p.stem)
        if cmd is not None:
            cmd.source = source
            out.append(cmd)
    return out


def _load_skills_dir(d: Path, source: str) -> list[Command]:
    out: list[Command] = []
    if not d.is_dir():
        return out
    for sub in sorted(d.iterdir()):
        if not sub.is_dir() or sub.name.startswith((".", "_")):
            continue
        for fname in ("SKILL.md", "skill.md"):
            p = sub / fname
            if p.is_file():
                cmd = parse_markdown_command(p, sub.name)
                if cmd is not None:
                    cmd.source = source
                    out.append(cmd)
                break
    return out


def global_root() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(xdg) / "funcode"


class MarkdownProvider(CommandProvider):
    """One directory tier: commands/*.md + skills/*/SKILL.md under given roots."""

    def __init__(self, name: str, cwd: Path, cmd_dir: Path | None,
                 skills_dir: Path | None, global_base: Path | None = None) -> None:
        self.name = name
        self.cwd = cwd
        self.cmd_dir = cmd_dir
        self.skills_dir = skills_dir
        self.global_base = global_base
        self._cache: list[Command] = []
        self.refresh()

    def refresh(self) -> None:
        out: list[Command] = []
        if self.cmd_dir is not None:
            out += _load_commands_dir(self.cmd_dir, f"{self.name}:commands")
        if self.skills_dir is not None:
            out += _load_skills_dir(self.skills_dir, f"{self.name}:skills")
        if self.global_base is not None:
            out += _load_commands_dir(self.global_base / "commands", f"{self.name}:commands")
            out += _load_skills_dir(self.global_base / "skills", f"{self.name}:skills")
        self._cache = out

    def list_commands(self) -> list[Command]:
        return list(self._cache)

    def reload(self) -> None:
        self.refresh()


def build_file_providers(cwd: Path) -> list[CommandProvider]:
    """Project → agents → compat → global. Register in this order so project wins."""
    g = global_root()
    providers: list[CommandProvider] = [
        MarkdownProvider("project", cwd, cwd / ".funcode" / "commands",
                         cwd / ".funcode" / "skills"),
        MarkdownProvider("agents", cwd, cwd / ".agents" / "commands",
                         cwd / ".agents" / "skills"),
    ]
    # Compat shims: only fill gaps (registry keeps first? no — later overrides).
    # Register compat BEFORE global/funcode-project so real funcode files win,
    # but AFTER agents? Compat is lowest trust of project tiers: put it right
    # after agents so .funcode project files (registered next round? no—).
    # Order here: agents, compat-claude, compat-opencode, project already first...
    # Fix: rebuild in strict low→high so add_provider override = project wins.
    compat: list[CommandProvider] = [
        MarkdownProvider("claude", cwd, cwd / ".claude" / "commands",
                         cwd / ".claude" / "skills"),
        MarkdownProvider("opencode", cwd, cwd / ".opencode" / "commands", None),
    ]
    project = providers[0]
    agents = providers[1]
    glob = MarkdownProvider("global", cwd, None, None, g)
    # Low → high priority registration order:
    return [*compat, glob, agents, project]
