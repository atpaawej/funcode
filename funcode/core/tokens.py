"""Token estimation + context-window resolution.

Heuristic by design: funcode talks to arbitrary OpenAI-compatible endpoints,
so exact tokenizer accounting is impossible. Estimates use tiktoken when
installed, else chars/4 — always labeled ~ in the UI.

Budget rule (per user spec):
- effective_budget = min(model_window, compact_cap)   # cap default 300k
- auto-compact triggers at fraction * effective_budget  # default 0.85
So a 1M model compacts at ~255k, a 128k model at ~109k.
"""
from __future__ import annotations

import re

from .types import Message, ToolDef

DEFAULT_WINDOW = 128_000
DEFAULT_CAP = 300_000
DEFAULT_FRACTION = 0.85

# Per-message overhead (role markers, tool framing). Matches OpenAI-style ~4.
_PER_MESSAGE = 4

# Substring match, ordered most-specific first. Values are context windows.
KNOWN_WINDOWS: tuple[tuple[str, int], ...] = (
    ("gpt-4.1", 1_000_000),
    ("gpt-4o-mini", 128_000),
    ("gpt-4o", 128_000),
    ("gpt-4-turbo", 128_000),
    ("gpt-4", 8_192),
    ("o1", 200_000),
    ("o3", 200_000),
    ("claude-opus-4", 200_000),
    ("claude-sonnet-4", 200_000),
    ("claude-3-7-sonnet", 200_000),
    ("claude-3-5-sonnet", 200_000),
    ("claude-3-5-haiku", 200_000),
    ("claude-3-haiku", 200_000),
    ("gemini-2.5", 1_000_000),
    ("gemini-2.0", 1_000_000),
    ("gemini-1.5", 1_000_000),
    ("deepseek-r1", 64_000),
    ("deepseek-v3", 64_000),
    ("deepseek", 64_000),
    ("qwen3", 256_000),
    ("qwen2.5", 32_768),
    ("qwen", 32_768),
    ("llama-3.3", 128_000),
    ("llama-3.1", 128_000),
    ("llama-3", 8_192),
    ("mistral-large", 128_000),
    ("mistral", 32_768),
    ("mixtral", 32_768),
    ("grok-2", 131_072),
    ("grok", 131_072),
    ("command-r", 128_000),
)


def _tiktoken_len(text: str, model: str = "") -> int | None:
    try:
        import tiktoken  # type: ignore

        try:
            enc = tiktoken.encoding_for_model(model)
        except Exception:
            enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return None


def estimate_text_tokens(text: str, model: str = "") -> int:
    """Estimate tokens for a string. Empty -> 0."""
    if not text:
        return 0
    n = _tiktoken_len(text, model)
    if n is not None:
        return n
    return max(1, len(text) // 4)


def count_messages_tokens(messages: list[Message], model: str = "",
                           tool_defs: list[ToolDef] | None = None) -> int:
    """Estimate prompt tokens for messages + tool schemas (what we'd send)."""
    total = 0
    for m in messages:
        total += _PER_MESSAGE
        total += estimate_text_tokens(m.content or "", model)
        for tc in m.tool_calls or []:
            total += _PER_MESSAGE
            total += estimate_text_tokens(tc.name or "", model)
            total += estimate_text_tokens(_args_text(tc.arguments), model)
        if m.tool_call_id:
            total += estimate_text_tokens(m.tool_call_id, model)
        if m.tool_name:
            total += estimate_text_tokens(m.tool_name, model)
    for t in tool_defs or []:
        total += _PER_MESSAGE * 2
        total += estimate_text_tokens(t.name or "", model)
        total += estimate_text_tokens(t.description or "", model)
        total += estimate_text_tokens(_args_text(t.parameters), model)
    return total


def _args_text(args: object) -> str:
    if args is None:
        return ""
    if isinstance(args, str):
        return args
    try:
        import json as _json
        return _json.dumps(args, ensure_ascii=False, default=str)
    except Exception:
        return str(args)


def resolve_window(model: str, override: int = 0) -> tuple[int, str]:
    """Return (window, source). Source: override | known | default."""
    if override and override > 0:
        return int(override), "override"
    name = (model or "").lower()
    for key, window in KNOWN_WINDOWS:
        if key in name:
            return window, "known"
    # Bare numeric hint like "...-128k" / "...-1m" in custom model names.
    m = re.search(r"(\d+)\s*([km])\b", name)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        window = n * (1_000_000 if unit == "m" else 1_000)
        if 4_000 <= window <= 10_000_000:
            return window, "name-hint"
    return DEFAULT_WINDOW, "default"


def effective_budget(window: int, cap: int = DEFAULT_CAP) -> int:
    """min(model window, cap). Cap<=0 means no cap."""
    window = max(1, int(window))
    cap = int(cap)
    if cap and cap > 0:
        return min(window, cap)
    return window


def compact_trigger_at(budget: int, fraction: float = DEFAULT_FRACTION) -> int:
    f = max(0.1, min(0.99, float(fraction)))
    return int(budget * f)


def format_k(n: int) -> str:
    if n >= 1_000_000:
        s = f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".")
        return f"{s}M"
    if n >= 1_000:
        s = f"{n / 1_000:.1f}".rstrip("0").rstrip(".")
        return f"{s}k"
    return str(n)
