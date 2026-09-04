# funcode

Personal extensible coding agent — small core, big seams. Raw ReAct loop today,
plugin system / MCP / TUI tomorrow, without rewriting the core.

Built with Python + Rich. Any OpenAI-compatible provider via `settings.json`.

## Quickstart

```bash
cp settings.example.json settings.json  # set base_url / api_key / model
uv run funcode "list files and read pyproject.toml"
uv run funcode          # REPL (/exit, /clear, /tools, /thinking, /verbose)
uv run funcode -y "..." # auto-approve write/dangerous tools
uv run funcode -v "..." # expanded thinking + full tool output
uv run funcode --no-thinking "..." # hide thinking blocks
uv run funcode --no-stream "..."   # one-shot fallback (no SSE)
```

`settings.json` also loads from `~/.config/funcode/settings.json`.
`web.tinyfish_api_key` (or `$TINYFISH_API_KEY`) enables `web_search` — free key at
https://agent.tinyfish.ai/api-keys. `web_fetch` needs no key.

## Tools

`read, write, edit, bash, glob, grep, web_fetch, web_search`

## UI

Token-by-token SSE streaming with live Markdown preview. Assistant text renders
as plain Markdown, tools collapse to two lines (`⏺ Read pyproject.toml` /
`⎿ 22 lines`), thinking is collapsed by default (`⋯ thought for 3s · 5 lines`).

- `/thinking [show|hide|full|collapse]` — toggle thinking display (REPL, display only)
- `/verbose` — expand thinking + full tool output (REPL)
- `agent.stream / show_thinking / verbose` in `settings.json` for defaults

## Architecture

The loop only knows abstractions — never SDKs:

- `funcode/core/provider.py` — `LLMProvider` ABC (`providers/openai_compat.py` is the
  only file importing `openai`, works with any OpenAI-compatible `base_url`)
- `funcode/core/tools.py` — `Tool` ABC (+ `risk: read|write|dangerous`), `ToolProvider`
  ABC, `ToolRegistry` (aggregates providers, validates args, fires hooks),
  `@tool` decorator (typed function → Tool with inferred JSON schema)
- `funcode/core/events.py` — `EventBus` (`pre_tool_use` / `post_tool_use`, blockable —
  permissions, logging, future plugins are just subscribers)
- `funcode/core/loop.py` — minimal ReAct loop + grounding rules
- `funcode/context/agents_md.py` — `AGENTS.md` / `CLAUDE.md` injected as prompt layer
- `funcode/ui/rich_cli.py` — Rich renderer (live Markdown, collapsed thinking)
- `funcode/ui/summaries.py` — per-tool one-line summaries (pure, no Rich)

New tools: subclass `Tool` (or use `@tool`) + add to a `ToolProvider`.
New providers (MCP, plugins): implement `ToolProvider` / `LLMProvider` — loop unchanged.

## Roadmap

sessions + resume → slash commands → MCP client → plugins → TUI.
