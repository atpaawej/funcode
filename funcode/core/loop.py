"""Minimal ReAct loop. Only talks to abstractions."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .compact import (
    CompactStats,
    apply_compaction,
    build_summary_messages,
    split_for_compact,
)
from .events import EventBus, ToolBlocked
from .provider import LLMProvider
from .tokens import (
    DEFAULT_CAP,
    DEFAULT_FRACTION,
    compact_trigger_at,
    count_messages_tokens,
    effective_budget,
    estimate_text_tokens,
    resolve_window,
)
from .tools import ToolContext, ToolRegistry
from .types import Message, ToolCall

BASE_SYSTEM = """You are funcode, a coding agent in a terminal.
You have tools: read, write, edit, bash, glob, grep, web_fetch, web_search.
Rules:
- Think step by step, then act with tools. Don't ask for confirmation in text; just call tools.
- Prefer read/glob/grep before editing. Keep answers concise.
- Use web_search for current/external facts, then web_fetch to read the best hits.
- For bash, avoid interactive/destructive commands unless asked.
- After tools finish, summarize what you did with file paths and URLs.
- If task is done with no tools needed, answer directly.
Grounding (no hallucinating):
- NEVER invent file paths, directories, tool outputs, versions, or environment facts.
- NEVER describe the machine, repo, or files from prior knowledge — only from what tools returned THIS session.
- When asked what you can do or see, describe capabilities + call tools (e.g. bash `pwd && ls`) before claiming anything about the environment.
- If you haven't called a tool yet, say so instead of guessing."""


class AgentLoop:
    def __init__(self, provider: LLMProvider, registry: ToolRegistry, bus: EventBus,
                 renderer, cwd: Path, max_turns: int = 25, project_notes: str = "",
                 stream: bool = True, context_window: int = 0,
                 compact_cap: int = DEFAULT_CAP,
                 auto_compact_fraction: float = DEFAULT_FRACTION,
                 compact_keep_last: int = 2):
        self.provider = provider
        self.registry = registry
        self.bus = bus
        self.ui = renderer
        self.cwd = cwd
        self.max_turns = max_turns
        self.project_notes = project_notes
        self.stream = stream
        self.messages: list[Message] = []
        self.context_window_override = max(0, int(context_window or 0))
        self.compact_cap = int(compact_cap or DEFAULT_CAP)
        self.auto_compact_fraction = float(auto_compact_fraction or DEFAULT_FRACTION)
        self.compact_keep_last = max(0, int(compact_keep_last))

    def _system(self) -> str:
        return BASE_SYSTEM + self.project_notes + f"\n\nWorking directory: {self.cwd}"

    def context_usage(self) -> dict:
        """Current prompt-token estimate + budget. Keys: used, window, source,
        budget, trigger_at, remaining, pct, plus breakdown keys
        system_tokens / tools_tokens / messages_tokens.

        Full-cost counting (like Claude Code / OpenCode): the system prompt is
        counted even before it is lazily inserted, since it WILL be sent."""
        window, source = resolve_window(self.provider.model, self.context_window_override)
        budget = effective_budget(window, self.compact_cap)
        trigger = compact_trigger_at(budget, self.auto_compact_fraction)
        model = self.provider.model
        defs = self.registry.defs()
        tools_tokens = count_messages_tokens([], model, defs)
        system_msgs = [m for m in self.messages if m.role == "system"]
        if system_msgs:
            system_tokens = count_messages_tokens(system_msgs, model)
        else:
            system_tokens = estimate_text_tokens(self._system(), model) + 4
        messages_tokens = count_messages_tokens(
            [m for m in self.messages if m.role != "system"], model)
        used = system_tokens + tools_tokens + messages_tokens
        return {"used": used, "window": window, "source": source, "budget": budget,
                "trigger_at": trigger, "remaining": max(0, trigger - used),
                "pct": (used / trigger * 100) if trigger else 0.0,
                "system_tokens": system_tokens, "tools_tokens": tools_tokens,
                "messages_tokens": messages_tokens, "n_tools": len(defs)}

    def compact(self, extra: str = "") -> CompactStats | None:
        """Summarize head, replace with summary message. Returns stats or None."""
        head, tail = split_for_compact(self.messages, self.compact_keep_last)
        if not head:
            return None
        with self.ui.thinking():
            result = self.provider.complete(build_summary_messages(head, extra), [])
        summary = (result.content or "").strip() or "(empty summary)"
        self.messages, stats = apply_compaction(
            self.messages, tail, summary, self.provider.model)
        self.bus.emit("compact", {"summary": summary, "before": stats.before_tokens,
                                  "after": stats.after_tokens, "kept_tail": stats.kept_tail})
        return stats

    def maybe_auto_compact(self) -> CompactStats | None:
        """Compact when estimate >= trigger. Returns stats if compacted."""
        usage = self.context_usage()
        if usage["used"] < usage["trigger_at"]:
            return None
        stats = self.compact()
        if stats is not None:
            self.ui.compact_notice(stats.before_tokens, stats.after_tokens,
                                    stats.kept_tail, auto=True)
        return stats

    def run(self, user_text: str) -> str:
        self.bus.emit("session_start", {"cwd": str(self.cwd), "model": self.provider.model})
        if not any(m.role == "system" for m in self.messages):
            self.messages.insert(0, Message(role="system", content=self._system()))
        self.messages.append(Message(role="user", content=user_text))
        self.bus.emit("message", {"role": "user", "content": user_text})
        self.bus.emit("turn_start", {"input": user_text})

        final = "(no response)"
        for _ in range(self.max_turns):
            self.maybe_auto_compact()
            self.bus.emit("llm_call", {})
            content, reasoning, tool_calls = self._stream_turn()
            if not tool_calls:
                self.messages.append(Message(role="assistant", content=content))
                self.bus.emit("message", {"role": "assistant", "content": content,
                                          "reasoning": reasoning, "tool_calls": []})
                final = content or final
                break
            self.messages.append(Message(role="assistant", content=content,
                                         tool_calls=tool_calls))
            self.bus.emit("message", {"role": "assistant", "content": content,
                                      "reasoning": reasoning,
                                      "tool_calls": [{"id": tc.id, "name": tc.name,
                                                      "arguments": tc.arguments}
                                                     for tc in tool_calls]})
            for tc in tool_calls:
                final = self._run_tool(tc) or final
        else:
            self.ui.warn(f"Stopped after {self.max_turns} turns.")
        self.bus.emit("turn_end", {"output": final})
        return final

    def _stream_turn(self) -> tuple[str, str, list[ToolCall]]:
        """Consume SSE stream, drive Live renderer. Returns (content, reasoning, calls)."""
        if not self.stream:
            return self._oneshot_turn()
        content_buf, reasoning_buf = "", ""
        finals: list[ToolCall] = []
        self.ui.stream_start()
        try:
            for ev in self.provider.complete_stream(self.messages, self.registry.defs()):
                kind = ev.kind
                if kind == "content":
                    content_buf += ev.text
                    self.ui.stream_token(ev.text)
                elif kind == "reasoning":
                    reasoning_buf += ev.text
                    self.ui.stream_reasoning(ev.text)
                elif kind == "tool_hint":
                    self.ui.stream_tool_hint(ev.tool_name, ev.args_fragment)
                elif kind == "tool_final":
                    try:
                        import json as _json
                        args = _json.loads(ev.args_fragment or "{}")
                    except Exception:
                        args = {"_raw": ev.args_fragment}
                    if not isinstance(args, dict):
                        args = {"_raw": args}
                    finals.append(ToolCall(id=ev.tool_id or f"call_{len(finals)}",
                                           name=ev.tool_name, arguments=args))
                elif kind == "error":
                    content, reasoning = self.ui.stream_end()
                    self.ui.warn(f"LLM error: {ev.error}")
                    return content or content_buf, reasoning or reasoning_buf, []
                elif kind == "done":
                    break
        except KeyboardInterrupt:
            self.ui.stream_cancel()
            raise
        except Exception as e:
            try:
                content, reasoning = self.ui.stream_end()
            except Exception:
                content, reasoning = content_buf, reasoning_buf
            self.ui.warn(f"LLM error: {e}")
            return content, reasoning, []
        content, reasoning = self.ui.stream_end()
        return content, reasoning, finals

    def _oneshot_turn(self) -> tuple[str, str, list[ToolCall]]:
        """Fallback for endpoints without SSE support."""
        with self.ui.thinking():
            result = self.provider.complete(self.messages, self.registry.defs())
        self.ui.thinking_text(result.reasoning)
        self.ui.assistant_text(result.content)
        return result.content, result.reasoning, result.tool_calls

    def _run_tool(self, tc: ToolCall) -> str:
        self.ui.tool_call(tc.name, tc.arguments)
        try:
            out = self.registry.execute(tc.name, tc.arguments, ToolContext(cwd=self.cwd))
        except ToolBlocked as e:
            out = f"Blocked: {e}. Told user it was not approved."
            self.ui.warn(str(e))
        self.ui.tool_result(tc.name, out[:4000], tc.arguments)
        self.messages.append(Message(role="tool", content=out,
                                     tool_call_id=tc.id, tool_name=tc.name))
        self.bus.emit("message", {"role": "tool", "name": tc.name, "args": tc.arguments,
                                  "result": out, "tool_call_id": tc.id})
        return out

    def load_history(self, messages: list[Message]) -> None:
        """Preload replayed transcript (resume). System prompt stays fresh."""
        self.messages = [m for m in messages if m.role != "system"]

    def reset(self) -> None:
        self.messages = []
