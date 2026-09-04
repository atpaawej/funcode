"""Tiny event bus. This is the extensibility seam for plugins/MCP later."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable


class ToolBlocked(Exception):
    pass


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[[dict], Any]]] = defaultdict(list)

    def on(self, event: str, fn: Callable[[dict], Any]) -> Callable[[dict], Any]:
        self._handlers[event].append(fn)
        return fn

    def emit(self, event: str, payload: dict | None = None) -> dict:
        data = payload or {}
        for fn in self._handlers.get(event, []):
            result = fn(data)
            # A listener can block tool execution by returning False.
            if result is False:
                raise ToolBlocked(f"blocked by {getattr(fn, '__name__', 'listener')}")
        return data
