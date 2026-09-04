"""Tool abstractions. Agent core owns these; providers/tools implement them.

- Tool: single callable unit (name, schema, risk, run)
- ToolProvider: groups tools by origin (builtin, web, mcp, plugin)
- ToolRegistry: aggregates providers, validates, fires hooks, executes
- @tool decorator: turn a function into a Tool with inferred JSON schema
"""
from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from .events import EventBus
from .types import ToolDef

Risk = Literal["read", "write", "dangerous"]


@dataclass
class ToolContext:
    """What a tool may touch. Grows over time; tools never get raw globals."""

    cwd: Path
    session_id: str = "default"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    output: str
    is_error: bool = False


class Tool(ABC):
    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}  # overridden per tool / instance
    risk: Risk = "read"

    @abstractmethod
    def run(self, args: dict[str, Any], ctx: ToolContext) -> str | ToolResult: ...

    def to_def(self) -> ToolDef:
        params = self.parameters or {"type": "object", "properties": {}}
        return ToolDef(name=self.name, description=self.description, parameters=params)

    def validate(self, args: dict[str, Any]) -> str | None:
        """Return error string if args invalid, else None."""
        if not isinstance(args, dict):
            return f"args must be an object, got {type(args).__name__}"
        required = (self.parameters or {}).get("required", [])
        missing = [k for k in required if k not in args]
        if missing:
            return f"missing required args: {', '.join(missing)}"
        return None


class ToolProvider(ABC):
    """A source of tools. Builtin/MCP/plugins all implement this."""

    name: str = "unknown"

    @abstractmethod
    def list_tools(self) -> list[Tool]: ...


# --- @tool decorator: function -> Tool with inferred schema ---

_PY_TO_JSON = {str: "string", int: "integer", float: "number", bool: "boolean",
               list: "array", dict: "object"}


def _infer_schema(fn: Callable) -> tuple[dict, list[str]]:
    sig = inspect.signature(fn)
    props: dict[str, Any] = {}
    required: list[str] = []
    for pname, p in sig.parameters.items():
        if pname == "ctx":
            continue
        ann = p.annotation
        jtype = _PY_TO_JSON.get(ann, "string") if ann is not inspect.Parameter.empty else "string"
        schema: dict[str, Any] = {"type": jtype}
        if p.default is not inspect.Parameter.empty:
            schema["default"] = p.default
        else:
            required.append(pname)
        props[pname] = schema
    return props, required


class FunctionTool(Tool):
    def __init__(self, fn: Callable, name: str = "", description: str = "",
                 parameters: dict | None = None, risk: Risk = "read"):
        self.fn = fn
        self.name = name or fn.__name__
        self.description = description or (inspect.getdoc(fn) or "").split("\n")[0]
        if parameters is not None:
            self.parameters = parameters
        else:
            props, required = _infer_schema(fn)
            self.parameters = {"type": "object", "properties": props, "required": required}
        self.risk = risk

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str | ToolResult:
        sig = inspect.signature(self.fn)
        kwargs = dict(args or {})
        if "ctx" in sig.parameters:
            kwargs["ctx"] = ctx
        return self.fn(**kwargs)


def tool(name: str = "", description: str = "", parameters: dict | None = None,
         risk: Risk = "read") -> Callable[[Callable], FunctionTool]:
    """Decorator: @tool(risk="write") def edit(path: str, ...) -> str"""

    def wrap(fn: Callable) -> FunctionTool:
        return FunctionTool(fn, name=name, description=description,
                            parameters=parameters, risk=risk)

    return wrap


# --- Registry: aggregates providers, validates, hooks, executes ---


class ToolRegistry:
    def __init__(self, bus: EventBus):
        self.bus = bus
        self._tools: dict[str, Tool] = {}
        self._providers: dict[str, ToolProvider] = {}

    def add_provider(self, provider: ToolProvider) -> None:
        self._providers[provider.name] = provider
        for t in provider.list_tools():
            self.register(t)

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def defs(self) -> list[ToolDef]:
        return [t.to_def() for t in self._tools.values()]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def execute(self, name: str, args: dict[str, Any], ctx: ToolContext | Path) -> str:
        if isinstance(ctx, Path):
            ctx = ToolContext(cwd=ctx)
        tool = self._tools.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'. Available: {', '.join(self.names())}"
        err = tool.validate(args or {})
        if err:
            return f"Error in {name}: {err}"
        self.bus.emit("pre_tool_use", {"tool": name, "args": args, "risk": tool.risk})
        try:
            out = tool.run(args or {}, ctx)
            text = out.output if isinstance(out, ToolResult) else str(out)
        except Exception as e:  # tools must never crash the loop
            text = f"Error in {name}: {e}"
        self.bus.emit("post_tool_use", {"tool": name, "args": args, "result": text})
        return text
