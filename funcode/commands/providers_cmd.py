"""The /providers command: interactive provider picker + safe key saving.

Proves the command seam: a Python ACTION command with prompts, registered
as a CommandProvider. Everything here is UI-side — no loop.run() call, so
keys never touch the transcript, the model, or tool outputs.
"""
from __future__ import annotations

import getpass

from . import Command, CommandContext, CommandProvider
from ..config import global_settings_path, project_settings_path, save_settings

PRESETS: list[dict] = [
    {"id": "openai", "label": "OpenAI",
     "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini", "needs_key": True},
    {"id": "openrouter", "label": "OpenRouter (many models, OpenAI-compat)",
     "base_url": "https://openrouter.ai/api/v1", "model": "openai/gpt-4o-mini",
     "needs_key": True},
    {"id": "groq", "label": "Groq (fast inference)",
     "base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile",
     "needs_key": True},
    {"id": "deepseek", "label": "DeepSeek",
     "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat",
     "needs_key": True},
    {"id": "ollama", "label": "Ollama (local, no key)",
     "base_url": "http://localhost:11434/v1", "model": "", "needs_key": False},
    {"id": "custom", "label": "Custom OpenAI-compatible endpoint",
     "base_url": "", "model": "", "needs_key": True},
]


def _mask(key: str) -> str:
    if not key:
        return "(not set)"
    if key.startswith("$"):
        return f"{key} (env var)"
    if len(key) <= 8:
        return "****"
    return f"****…{key[-4:]}"


def _ask(prompt: str, default: str = "") -> str | None:
    from ..ui.rich_cli import console
    hint = f" [{default}]" if default else ""
    try:
        raw = console.input(f"[bold cyan]? [/]{prompt}{hint} ").strip()
    except (KeyboardInterrupt, EOFError):
        return None
    return raw if raw else default


def _act_providers(ctx: CommandContext) -> None:
    from ..ui.rich_cli import console
    app = ctx.app
    ui = app.ui
    cwd = ctx.cwd

    settings = getattr(app, "settings", {}) or {}
    prov = settings.get("provider", {}) if isinstance(settings, dict) else {}
    if prov:
        ui.info(f"current: base_url={prov.get('base_url', '?')} · model={prov.get('model', '?')}"
                f" · key={_mask(str(prov.get('api_key', '')))}"
                f" · from {settings.get('_source', '?')}")
    else:
        try:
            ui.info(f"current model: {app.loop.provider.model}")
        except Exception:
            pass

    ui.info("Providers:")
    for i, p in enumerate(PRESETS, 1):
        ui.info(f"  {i}. {p['label']}")
    ui.info("  0. cancel")
    try:
        raw = console.input("[bold cyan]pick # [/]").strip()
    except (KeyboardInterrupt, EOFError):
        return
    if not raw.isdigit() or not (0 <= int(raw) <= len(PRESETS)):
        ui.warn("Cancelled." if raw == "0" else "Cancelled.")
        return
    if raw == "0":
        return
    preset = PRESETS[int(raw) - 1]

    base_url = preset["base_url"]
    if preset["id"] == "custom" or not base_url:
        base_url = _ask("base_url") or ""
        if base_url is None:
            return
        if not base_url:
            ui.warn("base_url is required.")
            return
    model = _ask("model", preset["model"])
    if model is None:
        return
    if not model:
        ui.warn("model is required.")
        return

    api_key = ""
    if preset["needs_key"]:
        try:
            api_key = getpass.getpass("api key (Enter = keep existing / use ${ENV_VAR}): ").strip()
        except (KeyboardInterrupt, EOFError):
            return
        if not api_key and preset["id"] == "custom":
            api_key = _ask("api key or ${ENV_VAR} reference") or ""
            if api_key is None:
                return
    else:
        api_key = "ollama"  # local server; OpenAI client just needs a non-empty string

    proj = project_settings_path(cwd)
    glob = global_settings_path()
    ui.info(f"  1. global  {glob}  (recommended for keys)")
    ui.info(f"  2. project {proj}")
    dest_raw = _ask("save to", "1")
    if dest_raw is None:
        return
    dest = glob if dest_raw.strip() != "2" else proj

    patch_provider: dict = {"base_url": base_url, "model": model}
    if api_key:
        patch_provider["api_key"] = api_key
    # Empty input + existing key at target: save_settings merge keeps it.
    saved = save_settings(dest, {"provider": patch_provider})
    ui.info(f"Saved provider ({preset['id']}) → {saved}")

    # Rebuild the live provider without restart.
    try:
        from ..providers.openai_compat import OpenAICompatProvider
        import json as _json
        data = _json.loads(saved.read_text())
        p = data.get("provider", {})
        key = str(p.get("api_key", ""))
        if key.startswith("$"):
            import os as _os
            key = _os.environ.get(key.lstrip("$").strip("{}"), "")
        app.loop.provider = OpenAICompatProvider(
            base_url=p.get("base_url", base_url), api_key=key or api_key,
            model=p.get("model", model))
        if isinstance(getattr(app, "settings", None), dict):
            app.settings["provider"] = {**p, "api_key": _mask(str(p.get("api_key", "")))}
        app.model = p.get("model", model)
        ui.info(f"Switched live session to {app.model}.")
    except Exception as e:
        ui.warn(f"Saved, but live switch failed (restart to apply): {e}")


class ProvidersProvider(CommandProvider):
    name = "providers"

    def list_commands(self) -> list[Command]:
        return [Command(name="providers",
                        description="Pick LLM provider + save API key safely",
                        kind="action", source="builtin",
                        action=_act_providers)]
