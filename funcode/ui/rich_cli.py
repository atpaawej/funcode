"""Rich CLI renderer. Loop depends on this interface, not on Rich directly.

Clean-A + SSE streaming:
- assistant text = plain Markdown, no panels
- tools = 2 compact lines (⏺ call / ⎿ result) via summaries.py
- thinking = collapsed by default (1 dim line), expand with verbose//thinking
- streaming = single Live region, throttled MD preview with fence guard
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm
from rich.text import Text

from .summaries import format_call, format_result

console = Console()

BRAND = r"""
 ███████╗██╗   ██╗███╗   ██╗ ██████╗ ██████╗ ██████╗ ███████╗
 ██╔════╝██║   ██║████╗  ██║██╔════╝██╔═══██╗██╔══██╗██╔════╝
 █████╗  ██║   ██║██╔██╗ ██║██║     ██║   ██║██║  ██║█████╗
 ██╔══╝  ██║   ██║██║╚██╗██║██║     ██║   ██║██║  ██║██╔══╝
 ██║     ╚██████╔╝██║ ╚████║╚██████╗╚██████╔╝██████╔╝███████╗
 ╚═╝      ╚═════╝ ╚═╝  ╚═══╝ ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝
""".rstrip()

TAGLINE = "personal extensible agent · v0 — small core, big seams"

# Throttle live MD re-renders (Markdown parse each token is wasteful).
_LIVE_MIN_INTERVAL = 0.12
# Expanded reasoning preview cap while streaming (full text kept for transcript).
_REASON_PREVIEW = 800


def _frag_args(name: str, frag: str) -> dict:
    """Best-effort args from a partial JSON shard. Falls back to regex so the
    live hint shows e.g. Read pyproject… instead of Read … mid-stream."""
    if frag:
        try:
            import json as _json
            args = _json.loads(frag)
            if isinstance(args, dict):
                return args
        except Exception:
            pass
        import re as _re
        for key in ("path", "command", "query", "pattern", "url"):
            m = _re.search(r'"' + key + r'"\s*:\s*"([^"\\]*)', frag)
            if m:
                return {key: m.group(1) + "…"}
    return {}


def _preview_md(buf: str) -> str:
    """Fence guard: close an unclosed ``` block + cursor for live preview."""
    if buf.count("```") % 2 == 1:
        return buf + "\n```\n▍"
    return buf + "▍" if buf and not buf.endswith("\n") else buf


class RichRenderer:
    def __init__(self, verbose: bool = False, show_thinking: bool = True):
        # verbose = expanded reasoning (full stream); show_thinking = collapsed line.
        self.verbose = verbose
        self.show_thinking = show_thinking
        # --- streaming state ---
        self._live: Live | None = None
        self._content = ""
        self._reasoning = ""
        self._hints: list[str] = []
        self._t0 = 0.0
        self._last_update = 0.0
        self._streaming = False

    # -- settings -----------------------------------------------------
    def set_verbose(self, v: bool) -> None:
        self.verbose = v

    def set_show_thinking(self, v: bool) -> None:
        self.show_thinking = v

    def toggle_thinking(self) -> bool:
        self.show_thinking = not self.show_thinking
        return self.show_thinking

    # -- startup ------------------------------------------------------
    def banner(self, subtitle: str = "") -> None:
        art = Text(BRAND, style="bold cyan")
        console.print(art)
        console.print(f"[dim]{TAGLINE}[/dim]")
        if subtitle:
            console.print(f"[dim]{subtitle}[/dim]")

    def startup(self, model: str, base_url: str, settings_src: str,
                cwd: str, tools: list[str], auto: bool) -> None:
        console.print(
            f"[dim]funcode · {model} · {len(tools)} tools · "
            f"{'auto-approve' if auto else 'approvals on'} · {cwd}[/dim]"
        )

    def startup_block(self, *, model: str, cwd: str, tools: list[str], auto: bool,
                      used: int, window: int, live: bool) -> None:
        """Organized REPL header: aligned status rows + grouped commands.

        live=False (no API call yet, like Claude Code's null current_usage):
        show the window reference only, no usage number."""
        from ..core.tokens import format_k
        pct = (used / window * 100) if window else 0.0
        console.print(f"[dim]model    [/dim]{model}")
        console.print(f"[dim]tools    [/dim]{len(tools)} · {', '.join(tools)}")
        console.print(f"[dim]mode     [/dim]{'auto-approve' if auto else 'approvals on'}")
        console.print(f"[dim]cwd      [/dim]{cwd}")
        if live:
            console.print(f"[dim]context  [/dim]~{format_k(used)}/{format_k(window)}"
                          f" ({pct:.0f}%)")
        else:
            console.print(f"[dim]context  [/dim]{format_k(window)} window")
        console.print()
        console.print("[dim]session   [/dim][dim]/new /resume /sessions /history /rename[/dim]")
        console.print("[dim]agent     [/dim][dim]/compact /context /tools /thinking /verbose[/dim]")
        console.print("[dim]general   [/dim][dim]/clear /exit[/dim]")

    def context_detail(self, usage: dict) -> None:
        """Claude-style breakdown: where the tokens actually go, plus the
        auto-compact threshold (its only visible home)."""
        from ..core.tokens import format_k
        self.context_bar(usage["used"], usage["window"])
        console.print(f"[dim]  system    ~{format_k(usage['system_tokens'])}"
                      f"  ·  tools  ~{format_k(usage['tools_tokens'])}"
                      f" ({usage['n_tools']})"
                      f"  ·  messages  ~{format_k(usage['messages_tokens'])}[/dim]")
        console.print(f"[dim]  auto-compact at ~{format_k(usage['trigger_at'])}[/dim]")

    # -- non-streaming fallbacks (history, errors, one-shot) ----------
    def thinking_text(self, text: str) -> None:
        if not self.show_thinking:
            return
        text = text.strip()
        if not text:
            return
        if not self.verbose:
            n = len(text.splitlines())
            console.print(f"[dim italic]⋯ thought · {n} lines — /thinking to expand[/dim italic]")
            return
        short = text if len(text) <= 3000 else text[:3000] + "\n…[truncated]"
        console.print(Panel(Text(short, style="dim italic"),
                            title="[dim]thinking[/dim]", border_style="dim", expand=False))

    def history_user(self, text: str) -> None:
        console.print(f"[bold cyan]› [/][dim]{text}[/dim]")

    def history_tool(self, name: str, output: str, max_lines: int = 10) -> None:
        console.print(f"[yellow]⏺ {name}[/yellow]")
        lines = output.splitlines() or ["(no output)"]
        if len(lines) > max_lines:
            body = "\n".join(lines[:max_lines])
            body += f"\n… ({len(lines) - max_lines} more lines in transcript)"
        else:
            body = "\n".join(lines)
        console.print(Panel(Text(body, style="dim"), title=f"⎿ {name}",
                            border_style="dim", expand=False))

    def render_history(self, messages, max_turns: int = 5) -> None:
        turns: list[list] = []
        for m in messages:
            if m.role == "system":
                continue
            if m.role == "user":
                turns.append([m])
            elif turns:
                turns[-1].append(m)
        if not turns:
            return
        if len(turns) > max_turns:
            console.print(f"[dim]… {len(turns) - max_turns} older turns hidden"
                          " — /history <n> to see more[/dim]")
        for turn in turns[-max_turns:]:
            for m in turn:
                if m.role == "user":
                    self.history_user(m.content)
                elif m.role == "assistant":
                    self.assistant_text(m.content)
                elif m.role == "tool":
                    summary, _ = format_result(m.tool_name or "tool", None, m.content)
                    console.print(f"[yellow]⏺ {m.tool_name or 'tool'}[/yellow]")
                    console.print(f"[dim]⎿ {summary}[/dim]")

    def resume_header(self, title: str, turns: int, age: str,
                      old_model: str, new_model: str) -> None:
        console.print(f"[dim]─── resumed '{title}' · {turns} turns · {age} ───[/dim]")
        if old_model and old_model != new_model:
            console.print(f"[yellow]! model changed since: {old_model} → {new_model}[/yellow]")

    def resume_footer(self) -> None:
        console.print("[dim]─── end of history · files may have changed ───[/dim]")

    def thinking(self):
        @contextmanager
        def _cm() -> Iterator[None]:
            with console.status("[cyan]thinking…", spinner="dots"):
                yield
        return _cm()

    def assistant_text(self, text: str) -> None:
        if text.strip():
            console.print(Markdown(text))

    # -- compact tool lines (non-stream path) -------------------------
    def tool_call(self, name: str, args: Any) -> None:
        label = format_call(name, args if isinstance(args, dict) else {})
        suffix = "" if isinstance(args, dict) else (f" {args}" if args else "")
        console.print(f"[yellow]⏺ {label}[/yellow][dim]{suffix}[/dim]")

    def tool_result(self, name: str, output: str, args: Any = None) -> None:
        summary, is_err = format_result(
            name, args if isinstance(args, dict) else None, output)
        style = "red" if is_err else "dim"
        console.print(f"[{style}]⎿ {summary}[/{style}]")
        if self.verbose and output.strip():
            lines = output.splitlines()[:20]
            console.print(Panel(Text("\n".join(lines), style="dim"),
                                title=f"⎿ {name}", border_style="dim", expand=False))

    # -- streaming ----------------------------------------------------
    def stream_start(self) -> None:
        self._content = ""
        self._reasoning = ""
        self._hints = []
        self._t0 = time.monotonic()
        self._last_update = 0.0
        self._streaming = True
        self._live = Live(self._render_stream(), console=console,
                          refresh_per_second=8, transient=True)
        self._live.start()

    def stream_reasoning(self, text: str) -> None:
        if not text:
            return
        self._reasoning += text
        self._maybe_update(force=False)

    def stream_token(self, text: str) -> None:
        if not text:
            return
        self._content += text
        self._maybe_update(force="\n" in text)

    def stream_tool_hint(self, name: str, args_frag: str = "") -> None:
        args = _frag_args(name, args_frag)
        label = format_call(name, args)
        if label not in self._hints:
            self._hints.append(label)
        self._maybe_update(force=True)

    def stream_end(self) -> tuple[str, str]:
        """Stop Live, print final clean blocks. Returns (content, reasoning)."""
        content, reasoning = self._content, self._reasoning
        elapsed = time.monotonic() - self._t0 if self._t0 else 0.0
        if self._live is not None:
            try:
                self._live.stop()
            except Exception:
                pass
            self._live = None
        self._streaming = False
        # collapsed thinking footer (1 line); expanded only when verbose
        if reasoning.strip() and self.show_thinking:
            n = len(reasoning.splitlines()) or 1
            if self.verbose:
                short = reasoning if len(reasoning) <= 3000 else reasoning[:3000] + "\n…[truncated]"
                console.print(Panel(Text(short, style="dim italic"),
                                    title="[dim]thinking[/dim]",
                                    border_style="dim", expand=False))
            else:
                console.print(
                    f"[dim italic]⋯ thought for {elapsed:.0f}s · {n} lines"
                    " — /thinking to expand[/dim italic]")
        if content.strip():
            console.print(Markdown(content))
        self._content, self._reasoning, self._hints = "", "", []
        return content, reasoning

    def stream_cancel(self) -> tuple[str, str]:
        if self._live is not None:
            try:
                self._live.stop()
            except Exception:
                pass
            self._live = None
        self._streaming = False
        content, reasoning = self._content, self._reasoning
        self._content, self._reasoning, self._hints = "", "", []
        return content, reasoning

    # -- internals ----------------------------------------------------
    def _maybe_update(self, force: bool = False) -> None:
        if self._live is None:
            return
        now = time.monotonic()
        if force or (now - self._last_update) >= _LIVE_MIN_INTERVAL:
            self._last_update = now
            try:
                self._live.update(self._render_stream())
            except Exception:
                pass

    def _render_stream(self) -> Group:
        parts: list[Any] = []
        if self._reasoning and self.show_thinking and not self._content and not self._hints:
            if self.verbose:
                preview = self._reasoning[-_REASON_PREVIEW:]
                parts.append(Text("⋯ thinking…", style="dim italic"))
                parts.append(Text(preview, style="dim italic"))
            else:
                n = len(self._reasoning.splitlines()) or 1
                parts.append(Text(f"◐ thinking… ({n} lines)", style="dim italic"))
        if self._content:
            try:
                parts.append(Markdown(_preview_md(self._content)))
            except Exception:
                parts.append(Text(self._content + "▍"))
        elif not parts:
            parts.append(Text("◐ …", style="dim"))
        for h in self._hints[-3:]:
            parts.append(Text(f"⏺ {h}", style="yellow"))
        return Group(*parts)

    # -- misc ---------------------------------------------------------
    def warn(self, text: str) -> None:
        console.print(f"[red]{text}[/red]")

    def context_bar(self, used: int, window: int) -> None:
        """Ambient budget line (statusline-style): used vs window only.
        The auto-compact threshold lives in /context detail, like Claude
        Code's Autocompact-buffer row — never in the ambient display."""
        from ..core.tokens import format_k
        pct = (used / window * 100) if window else 0.0
        left = max(0, window - used)
        if pct >= 100:
            style = "red"
        elif pct >= 80:
            style = "yellow"
        else:
            style = "dim"
        console.print(
            f"[{style}]ctx ~{format_k(used)}/{format_k(window)}"
            f" ({pct:.0f}%) · {format_k(left)} left[/{style}]"
        )

    def compact_notice(self, before: int, after: int, kept_tail: int, auto: bool = False) -> None:
        from ..core.tokens import format_k
        kind = "Auto-compacted" if auto else "Compacted"
        console.print(
            f"[green]{kind}: ~{format_k(before)} → ~{format_k(after)}"
            f" · kept last {kept_tail} turn(s) + summary[/green]"
        )

    def info(self, text: str) -> None:
        console.print(f"[dim]{text}[/dim]")

    def confirm(self, question: str) -> bool:
        try:
            return Confirm.ask(f"[bold yellow]?[/] {question}", default=False)
        except (KeyboardInterrupt, EOFError):
            return False

    def prompt(self) -> str:
        return console.input("[bold cyan]› [/]")
