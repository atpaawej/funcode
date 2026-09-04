"""AGENTS.md loader: walk up from cwd, inject as prompt layer."""
from __future__ import annotations

from pathlib import Path

NAMES = ("AGENTS.md", "CLAUDE.md", "CONVENTIONS.md")


def load_agents_md(cwd: Path) -> str:
    for d in (cwd, *cwd.parents):
        for name in NAMES:
            p = d / name
            if p.is_file():
                try:
                    text = p.read_text(errors="replace").strip()
                    if text:
                        return f"\n\n# Project conventions ({p.name})\n{text}"
                except OSError:
                    continue
    return ""
