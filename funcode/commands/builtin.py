"""Builtin ACTION commands. Migrated 1:1 from main.py REPL branches.

Each command is a Python callback (needs code, not a prompt). File-based
PROMPT commands live in markdown.py; plugins add more via CommandProvider.
"""
from __future__ import annotations

import time
from typing import Any

from . import Command, CommandContext, CommandProvider


def _age(ts: float) -> str:
    dt = time.time() - ts
    if dt < 3600:
        return f"{int(dt // 60)}m ago"
    if dt < 86400:
        return f"{int(dt // 3600)}h ago"
    return f"{int(dt // 86400)}d ago"


def _touch_index(app: Any) -> None:
    if app.turns == 0 and not app.recorder.materialized:
        return
    app.store.upsert(app.session_id, app.title, app.model, app.turns)


def _announce_resume(app: Any, msgs: list, meta: dict) -> None:
    age = _session_age(app, app.session_id)
    app.ui.resume_header(app.title, app.turns, age, meta.get("model", ""), app.model)
    if app.show_history:
        app.ui.render_history(msgs)
        app.ui.resume_footer()


def _session_age(app: Any, session_id: str) -> str:
    for d in app.store.list_sessions():
        if d["id"] == session_id:
            return _age(d.get("updated", 0))
    try:
        return _age(app.store.path_for(session_id).stat().st_mtime)
    except OSError:
        return "unknown age"


def _do_resume(app: Any, session_id: str) -> bool:
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
    from ..main import _wire_sessions as _wire
    app.recorder = _wire(app.loop.bus, app.store, session_id)
    _announce_resume(app, msgs, meta)
    return True


def _new_session(app: Any) -> None:
    from ..main import _wire_sessions as _wire
    app.recorder.close("new")
    _touch_index(app)
    app.loop.reset()
    from ..core.session import new_session_id
    app.session_id = new_session_id()
    app.title = "untitled"
    app.turns = 0
    app.recorder = _wire(app.loop.bus, app.store, app.session_id)
    app.ui.info(f"New session {app.session_id}. Previous one saved.")


def _pick_session(app: Any, filt: str = "") -> str | None:
    from ..ui.rich_cli import console
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


def _show_context(app: Any, detail: bool = False) -> None:
    try:
        u = app.loop.context_usage()
        if detail:
            app.ui.context_detail(u)
        else:
            app.ui.context_bar(u["used"], u["window"])
    except Exception:
        pass


# --- individual actions (each mirrors one old repl() branch) ---

def _act_new(ctx: CommandContext) -> None:
    _new_session(ctx.app)


def _act_sessions(ctx: CommandContext) -> None:
    app = ctx.app
    others = [d for d in app.store.list_sessions() if d["id"] != app.session_id][:15]
    if not others:
        app.ui.info("No other sessions.")
        return
    for d in others:
        app.ui.info(f"  {d.get('title', d['id'])}  ·  {_age(d.get('updated', 0))}"
                    f"  ·  {d.get('turns', 0)} turns")


def _act_resume(ctx: CommandContext) -> None:
    app = ctx.app
    filt = ctx.raw_args.strip()
    if filt:
        found = app.store.find(filt)
        if found and _do_resume(app, found["id"]):
            return
        app.ui.warn(f"No session matching '{filt}'.")
        return
    sid = _pick_session(app)
    if sid:
        _do_resume(app, sid)


def _act_history(ctx: CommandContext) -> None:
    app = ctx.app
    arg = ctx.raw_args.strip()
    try:
        n = int(arg) if arg else 5
    except ValueError:
        app.ui.warn("Usage: /history [n]")
        return
    app.ui.render_history(app.loop.messages, max_turns=max(1, n))


def _act_rename(ctx: CommandContext) -> None:
    app = ctx.app
    name = ctx.raw_args.strip()
    if name and app.store.rename(app.session_id, name):
        app.title = name
        app.ui.info(f"Renamed to '{name}'.")
    else:
        app.ui.warn("Usage: /rename <title>")


def _act_compact(ctx: CommandContext) -> None:
    app = ctx.app
    try:
        stats = app.loop.compact(ctx.raw_args.strip())
    except Exception as e:
        app.ui.warn(f"compact failed: {e}")
        return
    if stats is None:
        app.ui.info("Nothing to compact yet.")
    else:
        app.ui.compact_notice(stats.before_tokens, stats.after_tokens, stats.kept_tail)
        _show_context(app)


def _act_context(ctx: CommandContext) -> None:
    _show_context(ctx.app, detail=True)


def _act_tools(ctx: CommandContext) -> None:
    ctx.app.ui.info("tools: " + ", ".join(ctx.app.loop.registry.names()))


def _act_thinking(ctx: CommandContext) -> None:
    app = ctx.app
    arg = ctx.raw_args.strip().lower()
    if arg in ("show", "on"):
        app.ui.set_show_thinking(True)
    elif arg in ("hide", "off"):
        app.ui.set_show_thinking(False)
    elif arg in ("full", "expand", "verbose"):
        app.ui.set_show_thinking(True)
        app.ui.set_verbose(True)
    elif arg in ("collapse", "collapsed"):
        app.ui.set_verbose(False)
    elif arg == "":
        app.ui.toggle_thinking()
    else:
        app.ui.warn("Usage: /thinking [show|hide|full|collapse]")
        return
    state = "shown (collapsed)" if app.ui.show_thinking and not app.ui.verbose else \
        "expanded" if app.ui.show_thinking else "hidden"
    app.ui.info(f"thinking {state}.")


def _act_verbose(ctx: CommandContext) -> None:
    app = ctx.app
    app.ui.set_show_thinking(True)
    app.ui.set_verbose(not app.ui.verbose)
    app.ui.info(f"verbose {'on' if app.ui.verbose else 'off'}.")


def _act_clear(ctx: CommandContext) -> None:
    _new_session(ctx.app)


def _act_help(ctx: CommandContext) -> None:
    app = ctx.app
    registry = ctx.extra.get("registry")
    if registry is None:
        app.ui.info("commands: /help /exit")
        return
    cmds = registry.list_all()
    groups: dict[str, list] = {"session": [], "agent": [], "custom": [], "general": []}
    session_names = {"new", "clear", "resume", "sessions", "history", "rename"}
    agent_names = {"compact", "context", "ctx", "usage", "tools", "thinking",
                   "verbose", "providers"}
    for c in cmds:
        if c.name in session_names:
            groups["session"].append(c)
        elif c.name in agent_names:
            groups["agent"].append(c)
        elif c.name in ("help", "exit", "quit"):
            groups["general"].append(c)
        else:
            groups["custom"].append(c)
    for title in ("session", "agent", "custom", "general"):
        items = groups[title]
        if not items:
            continue
        parts = []
        for c in items:
            tag = f" ({c.source})" if title == "custom" else ""
            parts.append(f"/{c.name}{tag}")
        app.ui.info(f"{title}: " + "  ".join(parts))
    app.ui.info("Type /help <name> for detail on a custom command.")


def _act_help_detail(ctx: CommandContext) -> None:
    """Called by dispatcher when /help has args: show one command's description/source."""
    app = ctx.app
    registry = ctx.extra.get("registry")
    name = ctx.raw_args.strip().lstrip("/").lower()
    cmd = registry.get(name) if registry else None
    if cmd is None:
        app.ui.warn(f"Unknown command '/{name}'.")
        return
    loc = f"  ·  {cmd.path}" if cmd.path else ""
    app.ui.info(f"/{cmd.name} — {cmd.description or '(no description)'} ({cmd.source}){loc}")


class BuiltinProvider(CommandProvider):
    name = "builtin"

    def list_commands(self) -> list[Command]:
        def _a(name: str, desc: str, fn, aliases: list[str] | None = None) -> list[Command]:
            cmds = [Command(name=name, description=desc, kind="action",
                            source="builtin", action=fn)]
            for al in aliases or []:
                cmds.append(Command(name=al, description=f"alias of /{name}",
                                    kind="action", source="builtin", action=fn))
            return cmds

        return [
            *_a("new", "Start a new session (save current)", _act_new, ["clear"]),
            *_a("sessions", "List other saved sessions", _act_sessions),
            *_a("resume", "Resume a session: /resume [filter]", _act_resume),
            *_a("history", "Show recent turns: /history [n]", _act_history),
            *_a("rename", "Rename current session: /rename <title>", _act_rename),
            *_a("compact", "Summarize history to free context: /compact [focus]", _act_compact),
            *_a("context", "Show context breakdown + budget", _act_context,
                ["ctx", "usage"]),
            *_a("tools", "List available LLM tools", _act_tools),
            *_a("thinking", "Toggle thinking display: /thinking [show|hide|full|collapse]",
                _act_thinking),
            *_a("verbose", "Expand thinking + full tool output", _act_verbose),
            *_a("help", "List commands: /help [name]", _act_help),
            Command(name="exit", description="Exit funcode", kind="action",
                    source="builtin", action=lambda ctx: None),
            Command(name="quit", description="alias of /exit", kind="action",
                    source="builtin", action=lambda ctx: None),
        ]
