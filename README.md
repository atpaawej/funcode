# funcode

Personal extensible coding agent — small core, big seams. Raw ReAct loop today,
plugin system / MCP / TUI tomorrow, without rewriting the core.

Built with Python + Rich. Any OpenAI-compatible provider via `settings.json`.

## Quickstart

```bash
cp settings.example.json settings.json  # set base_url / api_key / model
uv run funcode "list files and read pyproject.toml"
uv run funcode          # REPL (/exit, /clear, /tools, /thinking, /verbose, /compact, /context)
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
- `/compact [focus]` — summarize history into one checkpoint, keep last turns
- `/context` — breakdown (`system · tools · messages`) plus budget line
- `agent.stream / show_thinking / verbose` in `settings.json` for defaults

## Context

Token use is estimated per turn (tiktoken when installed, else ~chars/4) and
shown after each reply as `ctx ~used/window (%)`, like OpenCode's
`30.0K / 200K (15%)`. Like Claude Code's status line, a fresh session shows
the window only (`128k window`) — live numbers appear once the first API call
exists. `/context` adds the breakdown (`system · tools · messages`) plus the
auto-compact threshold, mirroring Claude Code's Autocompact-buffer row.
Auto-compact fires at
`auto_compact_fraction × min(context_window, compact_cap)` — default
`0.85 × min(window, 300k)`, so 1M models compact at ~255k and 128k models at
~109k. Unknown models default to 128k; override with `agent.context_window`.
Summaries persist as `summary` checkpoints in the session JSONL, so resume
replays compacted — not full — history.

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
