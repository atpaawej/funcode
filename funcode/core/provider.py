"""Provider abstraction. Agent loop depends on this, not on openai/litellm."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator

from .types import AssistantResult, Message, ToolDef


@dataclass
class StreamEvent:
    """One SSE delta. kind: content | reasoning | tool_hint | done | error."""

    kind: str
    text: str = ""
    # tool_hint fields (partial, may arrive over many chunks)
    tool_index: int = 0
    tool_id: str = ""
    tool_name: str = ""
    args_fragment: str = ""
    error: str = ""


class LLMProvider(ABC):
    @property
    @abstractmethod
    def model(self) -> str: ...

    @abstractmethod
    def complete(self, messages: list[Message], tools: list[ToolDef]) -> AssistantResult: ...

    def complete_stream(
        self, messages: list[Message], tools: list[ToolDef]
    ) -> Iterator[StreamEvent]:
        """Token-by-token SSE stream. Default fallback: one-shot complete()."""
        try:
            result = self.complete(messages, tools)
        except Exception as e:
            yield StreamEvent(kind="error", error=str(e))
            return
        if result.reasoning:
            yield StreamEvent(kind="reasoning", text=result.reasoning)
        if result.content:
            yield StreamEvent(kind="content", text=result.content)
        for tc in result.tool_calls:
            import json as _json

            yield StreamEvent(
                kind="tool_final", tool_id=tc.id, tool_name=tc.name,
                args_fragment=_json.dumps(tc.arguments),
            )
        yield StreamEvent(kind="done")
