"""Minimal ReAct loop. Only talks to abstractions."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .events import EventBus, ToolBlocked
from .provider import LLMProvider
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
                 renderer, cwd: Path, max_turns: int = 25, project_notes: str = ""):
        self.provider = provider
        self.registry = registry
        self.bus = bus
        self.ui = renderer
        self.cwd = cwd
        self.max_turns = max_turns
        self.project_notes = project_notes
        self.messages: list[Message] = []

    def _system(self) -> str:
        return BASE_SYSTEM + self.project_notes + f"\n\nWorking directory: {self.cwd}"

    def run(self, user_text: str) -> str:
        self.bus.emit("session_start", {"cwd": str(self.cwd), "model": self.provider.model})
        if not any(m.role == "system" for m in self.messages):
            self.messages.insert(0, Message(role="system", content=self._system()))
        self.messages.append(Message(role="user", content=user_text))
        self.bus.emit("message", {"role": "user", "content": user_text})
        self.bus.emit("turn_start", {"input": user_text})

        final = "(no response)"
        for _ in range(self.max_turns):
            self.bus.emit("llm_call", {})
            with self.ui.thinking():
                result = self.provider.complete(self.messages, self.registry.defs())
            if result.reasoning:
                self.ui.thinking_text(result.reasoning)
            if not result.tool_calls:
                self.messages.append(Message(role="assistant", content=result.content))
                self.bus.emit("message", {"role": "assistant", "content": result.content,
                                          "reasoning": result.reasoning, "tool_calls": []})
                self.ui.assistant_text(result.content)
                final = result.content
                break
            self.messages.append(Message(role="assistant", content=result.content,
                                         tool_calls=result.tool_calls))
            self.bus.emit("message", {"role": "assistant", "content": result.content,
                                      "reasoning": result.reasoning,
                                      "tool_calls": [{"id": tc.id, "name": tc.name,
                                                      "arguments": tc.arguments}
                                                     for tc in result.tool_calls]})
            if result.content:
                self.ui.assistant_text(result.content)
            for tc in result.tool_calls:
                final = self._run_tool(tc) or final
        else:
            self.ui.warn(f"Stopped after {self.max_turns} turns.")
        self.bus.emit("turn_end", {"output": final})
        return final

    def _run_tool(self, tc: ToolCall) -> str:
        pretty_args = json.dumps(tc.arguments, indent=2)[:2000]
        self.ui.tool_call(tc.name, pretty_args)
        try:
            out = self.registry.execute(tc.name, tc.arguments, ToolContext(cwd=self.cwd))
        except ToolBlocked as e:
            out = f"Blocked: {e}. Told user it was not approved."
            self.ui.warn(str(e))
        self.ui.tool_result(tc.name, out[:4000])
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
