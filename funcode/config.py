"""Settings loader: any OpenAI-compatible endpoint via settings.json."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

_ENV_RE = re.compile(r"\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def _expand_env(s: str) -> str:
    def repl(m: re.Match) -> str:
        key = m.group(1) or m.group(2)
        return os.environ.get(key, "")
    return _ENV_RE.sub(repl, s)


def _expand(obj):
    if isinstance(obj, str):
        return _expand_env(obj)
    if isinstance(obj, dict):
        return {k: _expand(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand(v) for v in obj]
    return obj


def candidate_paths(explicit: str | None) -> list[Path]:
    paths: list[Path] = []
    if explicit:
        paths.append(Path(explicit))
    paths.append(Path.cwd() / "settings.json")
    xdg = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    paths.append(Path(xdg) / "funcode" / "settings.json")
    return paths


def load_settings(explicit: str | None = None) -> dict:
    for p in candidate_paths(explicit):
        if p.is_file():
            data = json.loads(p.read_text())
            data = _expand(data)
            data["_source"] = str(p)
            return data
    raise FileNotFoundError(
        "No settings.json found. Copy settings.example.json to settings.json "
        "or ~/.config/funcode/settings.json and set base_url/api_key/model."
    )
