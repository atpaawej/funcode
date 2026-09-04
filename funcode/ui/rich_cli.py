"""Rich CLI renderer. Loop depends on this interface, not on Rich directly."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm
from rich.text import Text

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


class RichRenderer:
    def banner(self, subtitle: str = "") -> None:
        art = Text(BRAND, style="bold cyan")
        console.print(art)
        console.print(f"[dim]{TAGLINE}[/dim]")
        if subtitle:
            console.print(f"[dim]{subtitle}[/dim]")

    def startup(self, model: str, base_url: str, settings_src: str,
                cwd: str, tools: list[str], auto: bool) -> None:
        self.banner()
        body = (
            f"[cyan]model[/cyan]    {model}\n"
            f"[cyan]endpoint[/cyan] {base_url}\n"
            f"[cyan]settings[/cyan] {settings_src}\n"
            f"[cyan]cwd[/cyan]      {cwd}\n"
            f"[cyan]tools[/cyan]    {', '.join(tools)}\n"
            f"[cyan]approvals[/cyan] {'OFF — auto-approve' if auto else 'ON for write/dangerous'}"
        )
        console.print(Panel(body, title="[bold]funcode[/bold]", border_style="cyan", expand=False))

    def thinking_text(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        short = text if len(text) <= 3000 else text[:3000] + "\n…[truncated]"
        console.print(Panel(Text(short, style="dim"), title="[dim]thinking[/dim]",
                            border_style="dim", expand=False))

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
        """Replay past turns: user echo, assistant panels, collapsed tools.
        Thinking traces skipped (still in model context). System msgs skipped."""
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
                    self.history_tool(m.tool_name or "tool", m.content)

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
            console.print(Panel(Markdown(text), title="funcode", border_style="cyan", expand=False))

    def tool_call(self, name: str, args: str) -> None:
        console.print(f"[yellow]⏺ {name}[/yellow][dim]{(' ' + args) if args else ''}[/dim]")

    def tool_result(self, name: str, output: str) -> None:
        short = output if len(output) <= 1200 else output[:1200] + "\n…[truncated]"
        console.print(Panel(short, title=f"⎿ {name}", border_style="dim", expand=False))

    def warn(self, text: str) -> None:
        console.print(f"[red]{text}[/red]")

    def info(self, text: str) -> None:
        console.print(f"[dim]{text}[/dim]")

    def confirm(self, question: str) -> bool:
        try:
            return Confirm.ask(f"[bold yellow]?[/] {question}", default=False)
        except (KeyboardInterrupt, EOFError):
            return False

    def prompt(self) -> str:
        return console.input("[bold cyan]› [/]")
