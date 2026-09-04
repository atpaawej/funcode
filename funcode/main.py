"""CLI entry: `uv run funcode [prompt]` or REPL."""
from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_settings
from .context.agents_md import load_agents_md
from .core.events import EventBus
from .core.loop import AgentLoop
from .core.tools import ToolRegistry
from .providers.openai_compat import OpenAICompatProvider
from .tools.builtin import BuiltinProvider
from .tools.web import WebProvider
from .ui.rich_cli import RichRenderer, console


def build(args) -> tuple[AgentLoop, RichRenderer, dict]:
    settings = load_settings(args.settings)
    p = settings.get("provider", {})
    a = settings.get("agent", {})
    w = settings.get("web", {})
    base_url = p.get("base_url", "")
    api_key = p.get("api_key", "")
    model = p.get("model", "")
    if not base_url or not model:
        raise SystemExit("settings.json needs provider.base_url and provider.model")
    if not api_key:
        raise SystemExit("settings.json needs provider.api_key (or ${ENV_VAR} that is set)")

    cwd = Path(args.cwd or a.get("cwd", ".")).resolve()
    max_turns = args.max_turns or int(a.get("max_turns", 25))
    auto = args.yes or bool(a.get("auto_approve", False))

    bus = EventBus()
    ui = RichRenderer()
    registry = ToolRegistry(bus)
    registry.add_provider(BuiltinProvider())
    registry.add_provider(WebProvider(tinyfish_api_key=w.get("tinyfish_api_key", "")))
    provider = OpenAICompatProvider(base_url=base_url, api_key=api_key, model=model)

    if not auto:
        def gate(payload: dict):
            # Risk comes from the tool itself, not a hardcoded list:
            # read = auto, write/dangerous = ask.
            tool = registry.get(payload.get("tool", ""))
            if tool is not None and tool.risk in ("write", "dangerous"):
                argstr = str(payload.get("args", ""))[:300]
                if not ui.confirm(f"Allow [{tool.risk}] [bold]{tool.name}[/bold] {argstr}?"):
                    return False  # EventBus turns this into ToolBlocked
        bus.on("pre_tool_use", gate)

    notes = load_agents_md(cwd)
    loop = AgentLoop(provider, registry, bus, ui, cwd, max_turns, notes)
    ui.startup(model, base_url, settings.get("_source", "?"),
               str(cwd), registry.names(), auto)
    return loop, ui, settings


def repl(loop: AgentLoop, ui: RichRenderer) -> None:
    ui.info("REPL — type /exit to quit, /clear to reset, /tools to list tools.")
    while True:
        try:
            text = ui.prompt().strip()
        except (KeyboardInterrupt, EOFError):
            break
        if not text:
            continue
        if text in ("/exit", "/quit"):
            break
        if text == "/clear":
            loop.reset()
            ui.info("context cleared.")
            continue
        if text == "/tools":
            ui.info("tools: " + ", ".join(loop.registry.names()))
            continue
        try:
            loop.run(text)
        except (KeyboardInterrupt, EOFError):
            ui.info("interrupted.")
            break
        except Exception as e:
            ui.warn(f"error: {e}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="funcode", description="funcode v0 — minimal extensible agent")
    ap.add_argument("prompt", nargs="*", help="one-shot task (empty = REPL)")
    ap.add_argument("--settings", help="path to settings.json")
    ap.add_argument("--cwd", help="working directory")
    ap.add_argument("--max-turns", type=int, default=None)
    ap.add_argument("-y", "--yes", action="store_true", help="auto-approve all tools")
    args = ap.parse_args()

    try:
        loop, ui, _ = build(args)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(2)

    if args.prompt:
        try:
            loop.run(" ".join(args.prompt))
        except Exception as e:
            ui.warn(f"error: {e}")
    else:
        repl(loop, ui)


if __name__ == "__main__":
    main()
