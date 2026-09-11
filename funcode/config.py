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


def project_settings_path(cwd: Path | None = None) -> Path:
    base = Path(cwd) if cwd is not None else Path.cwd()
    return base / "settings.json"


def global_settings_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(xdg) / "funcode" / "settings.json"


def save_settings(path: Path, patch: dict) -> Path:
    """Merge `patch` into the JSON file at `path` (created if missing).

    Preserves all other keys. `_source` is never written. When the merged
    settings contain a literal (non-${ENV}) provider.api_key, the file is
    chmodded 600. Returns the path written.
    """
    data: dict = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(data.get(k), dict):
            data[k] = {**data[k], **v}
        else:
            data[k] = v
    data.pop("_source", None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    try:
        key = (data.get("provider") or {}).get("api_key", "")
        if key and not (isinstance(key, str) and key.startswith("$")):
            os.chmod(path, 0o600)
    except OSError:
        pass
    return path
