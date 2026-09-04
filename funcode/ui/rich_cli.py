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
