"""OpenAI-compatible provider. ONLY file allowed to import openai."""
from __future__ import annotations

import json

from openai import OpenAI

from ..core.provider import LLMProvider
from ..core.types import AssistantResult, Message, ToolCall, ToolDef


class OpenAICompatProvider(LLMProvider):
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 120.0):
        self._model = model
        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)

    @property
    def model(self) -> str:
        return self._model

    def complete(self, messages: list[Message], tools: list[ToolDef]) -> AssistantResult:
        payload = [_to_wire(m) for m in messages]
        kwargs: dict = {}
        if tools:
            kwargs["tools"] = [
                {"type": "function", "function": {
                    "name": t.name, "description": t.description, "parameters": t.parameters,
                }} for t in tools
            ]
            kwargs["tool_choice"] = "auto"
        resp = self._client.chat.completions.create(model=self._model, messages=payload, **kwargs)
        choice = resp.choices[0].message
        calls: list[ToolCall] = []
        for tc in choice.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw": tc.function.arguments}
            calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        return AssistantResult(content=choice.content or "", tool_calls=calls,
                               reasoning=_reasoning(choice), raw=resp)


def _reasoning(msg) -> str:
    """Thinking models expose this as reasoning_content / reasoning (attr or extra)."""
    for key in ("reasoning_content", "reasoning"):
        val = getattr(msg, key, None)
        if isinstance(val, str) and val.strip():
            return val
    extra = getattr(msg, "model_extra", None) or {}
    for key in ("reasoning_content", "reasoning"):
        val = extra.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return ""


def _to_wire(m: Message) -> dict:
    if m.role == "tool":
        return {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content}
    if m.role == "assistant" and m.tool_calls:
        return {
            "role": "assistant",
            "content": m.content or None,
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                for tc in m.tool_calls
            ],
        }
    return {"role": m.role, "content": m.content}
