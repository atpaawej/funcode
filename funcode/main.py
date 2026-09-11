"""CLI entry: `uv run funcode [prompt]` or REPL. Sessions auto-save; resume via
`--resume` / `--continue` flags or `/resume` in REPL."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from .commands import CommandContext, CommandRegistry, split_command
from .commands.builtin import (
    BuiltinProvider as BuiltinCommands,
    _act_help_detail,
    _do_resume,
    _pick_session,
    _touch_index,
)
from .commands.markdown import build_file_providers
from .commands.providers_cmd import ProvidersProvider
from .config import load_settings
from .context.agents_md import load_agents_md
from .core.events import EventBus
from .core.loop import AgentLoop
from .core.session import SessionRecorder, SessionStore, make_title, new_session_id
from .core.tools import ToolRegistry
from .providers.openai_compat import OpenAICompatProvider
from .tools.builtin import BuiltinProvider
from .tools.web import WebProvider
from .ui.rich_cli import RichRenderer, console


@dataclass
class App:
    loop: AgentLoop
    ui: RichRenderer
    store: SessionStore
    recorder: SessionRecorder
    session_id: str
    title: str
    model: str
    settings: dict
    cmd_registry: CommandRegistry | None = None
    turns: int = 0
    show_history: bool = True


def _wire_sessions(bus: EventBus, store: SessionStore, session_id: str) -> SessionRecorder:
    rec = SessionRecorder(store.path_for(session_id), session_id)
    bus.on("session_start", rec.on_session_start)
    bus.on("message", rec.on_message)
    bus.on("compact", rec.on_compact)
    return rec


def build(args) -> App:
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
    verbose = bool(args.verbose or a.get("verbose", False))
    # show_thinking: --no-thinking hides, --thinking forces collapsed show, default on.
    if args.no_thinking:
        show_thinking = False
    elif args.thinking:
        show_thinking = True
    else:
        show_thinking = bool(a.get("show_thinking", True))
    if verbose:
        show_thinking = True
    stream = (not args.no_stream) and bool(a.get("stream", True))

    bus = EventBus()
    ui = RichRenderer(verbose=verbose, show_thinking=show_thinking)
    registry = ToolRegistry(bus)
    registry.add_provider(BuiltinProvider())
    registry.add_provider(WebProvider(tinyfish_api_key=w.get("tinyfish_api_key", "")))
    provider = OpenAICompatProvider(base_url=base_url, api_key=api_key, model=model)

    # Slash commands: builtin actions first (lowest priority), then the
    # /providers showcase, then file-based tiers low→high so project wins.
    cregistry = CommandRegistry()
    cregistry.add_provider(BuiltinCommands())
    cregistry.add_provider(ProvidersProvider())
    for fp in build_file_providers(cwd):
        cregistry.add_provider(fp)

    if not auto:
        def gate(payload: dict):
            tool = registry.get(payload.get("tool", ""))
            if tool is not None and tool.risk in ("write", "dangerous"):
                argstr = str(payload.get("args", ""))[:300]
                if not ui.confirm(f"Allow [{tool.risk}] [bold]{tool.name}[/bold] {argstr}?"):
                    return False
        bus.on("pre_tool_use", gate)

    notes = load_agents_md(cwd)
    loop = AgentLoop(provider, registry, bus, ui, cwd, max_turns, notes, stream=stream,
                     context_window=int(a.get("context_window", 0) or 0),
                     compact_cap=int(a.get("compact_cap", 300_000) or 300_000),
                     auto_compact_fraction=float(a.get("auto_compact_fraction", 0.85) or 0.85),
                     compact_keep_last=int(a.get("compact_keep_last", 2)))
    store = SessionStore(cwd)
    store.cleanup()

    # Resolve starting session: --continue > --resume > fresh.
    session_id, title, turns = new_session_id(), "untitled", 0
    resumed = None
    if args.continue_flag:
        latest = store.most_recent()
        if latest:
            session_id = latest["id"]
    elif args.resume:
        found = store.find(args.resume)
        if found:
            session_id = found["id"]
        else:
            ui.warn(f"No session matching '{args.resume}' — starting fresh.")
    if session_id != "" and (args.continue_flag or args.resume):
        msgs, meta = store.load_messages(session_id)
        if msgs:
            loop.load_history(msgs)
            title, turns = meta.get("title") or session_id, meta.get("turns", 0)
            resumed = (msgs, meta)
        else:
            session_id, title, turns = new_session_id(), "untitled", 0
            resumed = None

    app = App(loop=loop, ui=ui, store=store,
              recorder=_wire_sessions(bus, store, session_id),
              session_id=session_id, title=title, model=model, turns=turns,
              settings=settings, cmd_registry=cregistry,
              show_history=not args.no_history)
    if resumed is not None:
        _announce_resume(app, *resumed)
    # Branding: banner by default in interactive REPL; --no-banner hides it,
    # --banner forces it (also for one-shot).
    show_banner = getattr(args, "banner", False) or (
            not getattr(args, "no_banner", False) and not args.prompt)
    if show_banner:
        ui.banner()
    if not args.prompt:
        try:
            u = loop.context_usage()
            custom = [c.name for c in cregistry.list_all()
                      if not c.source.startswith("builtin")
                      and c.source not in ("builtin", "providers")]
            ui.startup_block(model=model, cwd=str(cwd), tools=registry.names(),
                             auto=auto, used=u["used"],
                             window=u["window"], live=bool(loop.messages),
                             custom_commands=custom)
        except Exception:
            ui.startup(model, base_url, settings.get("_source", "?"),
                       str(cwd), registry.names(), auto)
    else:
        ui.startup(model, base_url, settings.get("_source", "?"),
                   str(cwd), registry.names(), auto)
    return app


def _after_turn(app: App, user_text: str) -> None:
    if app.title == "untitled" and user_text and not user_text.startswith("/"):
        app.title = make_title(user_text)
    app.turns += 1
    _touch_index(app)
    _show_context(app)


def _show_context(app: App, detail: bool = False) -> None:
    try:
        u = app.loop.context_usage()
        if detail:
            app.ui.context_detail(u)
        else:
            app.ui.context_bar(u["used"], u["window"])
    except Exception:
        pass


def _dispatch_command(app: App, text: str) -> bool:
    """Run one slash command. Returns True when the REPL should exit."""
    name, raw = split_command(text)
    cmd = app.cmd_registry.get(name) if app.cmd_registry is not None else None
    if cmd is None:
        app.ui.warn(f"Unknown command '/{name}'. Type /help.")
        return False
    ctx = CommandContext(cwd=app.loop.cwd, raw_args=raw, app=app,
                         extra={"registry": app.cmd_registry})
    if cmd.is_action:
        if cmd.name == "help" and raw.strip():
            _act_help_detail(ctx)
            return False
        if cmd.name in ("exit", "quit"):
            return True
        try:
            if cmd.action is not None:
                cmd.action(ctx)
        except (KeyboardInterrupt, EOFError):
            app.ui.info("cancelled.")
        except Exception as e:
            app.ui.warn(f"error: {e}")
        return False
    # PROMPT command: expand template, run through the loop with provenance.
    assert app.cmd_registry is not None
    expanded = app.cmd_registry.expand(cmd, raw, app.loop.cwd)
    try:
        app.loop.run(f"[command /{cmd.name}]\n{expanded}")
        _after_turn(app, text)
    except (KeyboardInterrupt, EOFError):
        raise
    except Exception as e:
        app.ui.warn(f"error: {e}")
    return False


def repl(app: App) -> None:
    loop, ui = app.loop, app.ui
    while True:
        try:
            if app.cmd_registry is not None:
                app.cmd_registry.reload()
                descs = {c.name: (c.description or "", c.source)
                         for c in app.cmd_registry.list_all()}
                text = ui.prompt(commands=descs, cwd=loop.cwd).strip()
            else:
                text = ui.prompt().strip()
        except (KeyboardInterrupt, EOFError):
            break
        if not text:
            continue
        if text.startswith("/"):
            try:
                if _dispatch_command(app, text):
                    break
            except (KeyboardInterrupt, EOFError):
                ui.info("interrupted.")
                break
            except Exception as e:
                ui.warn(f"error: {e}")
            continue
        try:
            loop.run(text)
            _after_turn(app, text)
        except (KeyboardInterrupt, EOFError):
            ui.info("interrupted.")
            break
        except Exception as e:
            ui.warn(f"error: {e}")
    app.recorder.close("exit")
    _touch_index(app)


def main() -> None:
    ap = argparse.ArgumentParser(prog="funcode", description="funcode v0 — minimal extensible agent")
    ap.add_argument("prompt", nargs="*", help="one-shot task (empty = REPL)")
    ap.add_argument("--settings", help="path to settings.json")
    ap.add_argument("--cwd", help="working directory")
    ap.add_argument("--max-turns", type=int, default=None)
    ap.add_argument("-y", "--yes", action="store_true", help="auto-approve all tools")
    ap.add_argument("--resume", metavar="SESSION", nargs="?", const="",
                    help="resume a session by id-prefix or title (empty = picker in REPL)")
    ap.add_argument("-c", "--continue", dest="continue_flag", action="store_true",
                    help="resume the most recent session")
    ap.add_argument("--no-history", action="store_true",
                    help="skip printing previous turns on resume")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="expand thinking + full tool output")
    ap.add_argument("--thinking", action="store_true",
                    help="show thinking (collapsed)")
    ap.add_argument("--no-thinking", action="store_true",
                    help="hide thinking blocks")
    ap.add_argument("--banner", action="store_true",
                    help="show ASCII banner at startup (default in REPL)")
    ap.add_argument("--no-banner", action="store_true",
                    help="hide ASCII banner at startup")
    ap.add_argument("--no-stream", action="store_true",
                    help="disable SSE streaming (one-shot fallback)")
    args = ap.parse_args()

    try:
        app = build(args)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(2)

    if args.prompt:
        try:
            # Bare --resume flag with a one-shot prompt = most recent.
            if args.resume == "" and not args.continue_flag:
                latest = app.store.most_recent()
                if latest and latest["id"] != app.session_id:
                    _do_resume(app, latest["id"])
            prompt = " ".join(args.prompt)
            if prompt.strip().startswith("/"):
                _dispatch_command(app, prompt)
            else:
                app.loop.run(prompt)
                _after_turn(app, prompt)
        except Exception as e:
            app.ui.warn(f"error: {e}")
        finally:
            app.recorder.close("exit")
            _touch_index(app)
    else:
        if args.resume == "":
            sid = _pick_session(app)
            if sid:
                _do_resume(app, sid)
        repl(app)


if __name__ == "__main__":
    main()
