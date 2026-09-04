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

    def complete_stream(self, messages: list[Message], tools: list[ToolDef]):
        """SSE token stream. Yields content/reasoning/tool_hint, ends with done/error."""
        from ..core.provider import StreamEvent

        payload = [_to_wire(m) for m in messages]
        kwargs: dict = {}
        if tools:
            kwargs["tools"] = [
                {"type": "function", "function": {
                    "name": t.name, "description": t.description, "parameters": t.parameters,
                }} for t in tools
            ]
            kwargs["tool_choice"] = "auto"
        try:
            stream = self._client.chat.completions.create(
                model=self._model, messages=payload, stream=True, **kwargs)
        except Exception as e:
            yield StreamEvent(kind="error", error=str(e))
            return
        acc = _ToolAccumulator()
        try:
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                # reasoning (thinking models): reasoning_content / reasoning
                for key in ("reasoning_content", "reasoning"):
                    val = getattr(delta, key, None)
                    if isinstance(val, str) and val:
                        yield StreamEvent(kind="reasoning", text=val)
                        break
                content = getattr(delta, "content", None)
                if content:
                    yield StreamEvent(kind="content", text=content)
                tcs = getattr(delta, "tool_calls", None) or []
                for tc in tcs:
                    fn = getattr(tc, "function", None)
                    frag = getattr(fn, "arguments", "") or "" if fn else ""
                    hint = acc.add(
                        index=getattr(tc, "index", 0),
                        tc_id=getattr(tc, "id", "") or "",
                        name=getattr(fn, "name", "") or "" if fn else "",
                        frag=frag if isinstance(frag, str) else "",
                    )
                    if hint is not None:
                        yield hint
        except Exception as e:
            yield StreamEvent(kind="error", error=str(e))
            return
        # flush any remaining hints (some servers send tool_calls in one chunk)
        for hint in acc.flush():
            yield hint
        # emit final assembled calls so the loop has full args without a 2nd request
        for tc in acc.tool_calls():
            yield StreamEvent(kind="tool_final", tool_index=0,
                               tool_id=tc.id, tool_name=tc.name,
                               args_fragment=json.dumps(tc.arguments))
        yield StreamEvent(kind="done")


class _ToolAccumulator:
    """Coalesce sharded tool_calls. Emits a hint once per tool when name/first
    args arrive, then silently accumulates the rest (loop reads final args at done)."""

    def __init__(self) -> None:
        self._by_index: dict[int, dict] = {}
        self._emitted: set[int] = set()

    def add(self, index: int, tc_id: str, name: str, frag: str):
        from ..core.provider import StreamEvent

        slot = self._by_index.setdefault(index, {"id": "", "name": "", "args": ""})
        if tc_id:
            slot["id"] = tc_id
        if name:
            slot["name"] = name
        if frag:
            slot["args"] += frag
        if index not in self._emitted and slot["name"]:
            self._emitted.add(index)
            return StreamEvent(kind="tool_hint", tool_index=index,
                               tool_id=slot["id"], tool_name=slot["name"],
                               args_fragment=frag)
        return None

    def flush(self):
        from ..core.provider import StreamEvent

        out = []
        for i, slot in self._by_index.items():
            if i not in self._emitted and slot["name"]:
                self._emitted.add(i)
                out.append(StreamEvent(kind="tool_hint", tool_index=i,
                                       tool_id=slot["id"], tool_name=slot["name"]))
        return out

    def tool_calls(self) -> list[ToolCall]:
        """Final assembled calls after stream ends (args JSON may still be partial)."""
        calls: list[ToolCall] = []
        for i in sorted(self._by_index):
            slot = self._by_index[i]
            if not slot["name"]:
                continue
            try:
                args = json.loads(slot["args"] or "{}")
            except json.JSONDecodeError:
                args = {"_raw": slot["args"]}
            calls.append(ToolCall(id=slot["id"] or f"call_{i}", name=slot["name"],
                                  arguments=args if isinstance(args, dict) else {"_raw": args}))
        return calls


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
