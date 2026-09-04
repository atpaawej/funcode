"""Provider abstraction. Agent loop depends on this, not on openai/litellm."""
from __future__ import annotations

from abc import ABC, abstractmethod

from .types import AssistantResult, Message, ToolDef


class LLMProvider(ABC):
    @property
    @abstractmethod
    def model(self) -> str: ...

    @abstractmethod
    def complete(self, messages: list[Message], tools: list[ToolDef]) -> AssistantResult: ...
