"""CLI entry: `uv run funcode [prompt]` or REPL. Sessions auto-save; resume via
`--resume` / `--continue` flags or `/resume` in REPL."""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from pathlib import Path

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
    turns: int = 0
    show_history: bool = True


def _wire_sessions(bus: EventBus, store: SessionStore, session_id: str) -> SessionRecorder:
    rec = SessionRecorder(store.path_for(session_id), session_id)
    bus.on("session_start", rec.on_session_start)
    bus.on("message", rec.on_message)
    return rec


def _touch_index(app: App) -> None:
    # Lazy sessions (no user message yet) leave no trace: no file, no index row.
    if app.turns == 0 and not app.recorder.materialized:
        return
    app.store.upsert(app.session_id, app.title, app.model, app.turns)


def _session_age(app: App, session_id: str) -> str:
    for d in app.store.list_sessions():
        if d["id"] == session_id:
            return _age(d.get("updated", 0))
    try:
        return _age(app.store.path_for(session_id).stat().st_mtime)
    except OSError:
        return "unknown age"


def _announce_resume(app: App, msgs: list, meta: dict) -> None:
    age = _session_age(app, app.session_id)
    app.ui.resume_header(app.title, app.turns, age, meta.get("model", ""), app.model)
    if app.show_history:
        app.ui.render_history(msgs)
        app.ui.resume_footer()


def _do_resume(app: App, session_id: str) -> bool:
    msgs, meta = app.store.load_messages(session_id)
    if not msgs:
        app.ui.warn(f"No transcript found for '{session_id}'.")
        return False
    app.recorder.close("switch")
    _touch_index(app)
    app.loop.load_history(msgs)
    app.session_id = session_id
    app.title = meta.get("title") or session_id
    app.turns = meta.get("turns", 0)
    app.recorder = _wire_sessions(app.loop.bus, app.store, session_id)
    _announce_resume(app, msgs, meta)
    return True


def _new_session(app: App) -> None:
    app.recorder.close("new")
    _touch_index(app)
    app.loop.reset()
    app.session_id = new_session_id()
    app.title = "untitled"
    app.turns = 0
    app.recorder = _wire_sessions(app.loop.bus, app.store, app.session_id)
    app.ui.info(f"New session {app.session_id}. Previous one saved.")


def _pick_session(app: App, filt: str = "") -> str | None:
    sessions = app.store.list_sessions()
    if filt:
        q = filt.lower()
        sessions = [d for d in sessions
                    if q in d.get("title", "").lower() or q in d["id"].lower()]
    sessions = [d for d in sessions if d["id"] != app.session_id][:15]
    if not sessions:
        app.ui.warn("No other sessions found.")
        return None
    app.ui.info("Sessions (recent first):")
    for i, d in enumerate(sessions, 1):
        age = _age(d.get("updated", 0))
        app.ui.info(f"  {i}. {d.get('title', d['id'])}  ·  {age}  ·  {d.get('turns', 0)} turns")
    try:
        raw = console.input("[bold cyan]resume # (or Enter to cancel) [/]").strip()
    except (KeyboardInterrupt, EOFError):
        return None
    if not raw:
        return None
    if raw.isdigit() and 1 <= int(raw) <= len(sessions):
        return sessions[int(raw) - 1]["id"]
    found = app.store.find(raw)
    return found["id"] if found else None


def _age(ts: float) -> str:
    dt = time.time() - ts
    if dt < 3600:
        return f"{int(dt // 60)}m ago"
    if dt < 86400:
        return f"{int(dt // 3600)}h ago"
    return f"{int(dt // 86400)}d ago"


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

    bus = EventBus()
    ui = RichRenderer()
    registry = ToolRegistry(bus)
    registry.add_provider(BuiltinProvider())
    registry.add_provider(WebProvider(tinyfish_api_key=w.get("tinyfish_api_key", "")))
    provider = OpenAICompatProvider(base_url=base_url, api_key=api_key, model=model)

    if not auto:
        def gate(payload: dict):
            tool = registry.get(payload.get("tool", ""))
            if tool is not None and tool.risk in ("write", "dangerous"):
                argstr = str(payload.get("args", ""))[:300]
                if not ui.confirm(f"Allow [{tool.risk}] [bold]{tool.name}[/bold] {argstr}?"):
                    return False
        bus.on("pre_tool_use", gate)

    notes = load_agents_md(cwd)
    loop = AgentLoop(provider, registry, bus, ui, cwd, max_turns, notes)
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
              show_history=not args.no_history)
    if resumed is not None:
        _announce_resume(app, *resumed)
    ui.startup(model, base_url, settings.get("_source", "?"),
               str(cwd), registry.names(), auto)
    return app


def _after_turn(app: App, user_text: str) -> None:
    if app.title == "untitled" and user_text and not user_text.startswith("/"):
        app.title = make_title(user_text)
    app.turns += 1
    _touch_index(app)


def repl(app: App) -> None:
    loop, ui = app.loop, app.ui
    ui.info("REPL — /exit /clear /new /resume [filter] /sessions /history [n] /rename <t> /tools.")
    while True:
        try:
            text = ui.prompt().strip()
        except (KeyboardInterrupt, EOFError):
            break
        if not text:
            continue
        if text in ("/exit", "/quit"):
            break
        if text in ("/clear", "/new"):
            _new_session(app)
            continue
        if text == "/sessions":
            for d in app.store.list_sessions()[:15]:
                mark = "*" if d["id"] == app.session_id else " "
                ui.info(f"{mark} {d.get('title', d['id'])}  ·  {_age(d.get('updated', 0))}"
                        f"  ·  {d.get('turns', 0)} turns")
            continue
        if text == "/resume" or text.startswith("/resume "):
            filt = text[len("/resume"):].strip()
            if filt:
                found = app.store.find(filt)
                if found and _do_resume(app, found["id"]):
                    continue
                ui.warn(f"No session matching '{filt}'.")
                continue
            sid = _pick_session(app)
            if sid:
                _do_resume(app, sid)
            continue
        if text == "/history" or text.startswith("/history "):
            arg = text[len("/history"):].strip()
            try:
                n = int(arg) if arg else 5
            except ValueError:
                ui.warn("Usage: /history [n]")
                continue
            ui.render_history(loop.messages, max_turns=max(1, n))
            continue
        if text.startswith("/rename"):
            name = text[len("/rename"):].strip()
            if name and app.store.rename(app.session_id, name):
                app.title = name
                ui.info(f"Renamed to '{name}'.")
            else:
                ui.warn("Usage: /rename <title>")
            continue
        if text == "/tools":
            ui.info("tools: " + ", ".join(loop.registry.names()))
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
