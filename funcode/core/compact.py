"""Conversation compaction: summarize head, keep system + tail.

Manual: /compact [extra instructions]
Auto: loop calls maybe_auto_compact() before each LLM call; when estimated
prompt tokens >= trigger, the head is summarized via a non-streaming call and
replaced by a single summary message. Tail (recent turns) is preserved verbatim
so tool_call/tool_result pairs stay intact.
"""
from __future__ import annotations

from dataclasses import dataclass

from .tokens import count_messages_tokens
from .types import Message

# Per-message truncation when building the summarize prompt: keeps the summary
# call small even when the head holds huge tool outputs.
_SUMMARIZE_TRUNC = 2_000

SUMMARY_PREFIX = "Previous session summary (compacted context — treat as ground truth):\n"


@dataclass
class CompactStats:
    before_tokens: int
    after_tokens: int
    kept_tail: int
    summary_chars: int


def split_for_compact(messages: list[Message], keep_last_user_turns: int = 2) -> tuple[list[Message], list[Message]]:
    """Split into (head, tail). System message is never in the head.

    Tail = system + last N user turns (with their assistant/tool followers).
    Head = everything else. If there is nothing worth summarizing, head is [].
    """
    keep = max(0, int(keep_last_user_turns))
    if len(messages) <= 1:
        return [], list(messages)
    system = messages[:1] if messages[0].role == "system" else []
    rest = messages[1:] if system else list(messages)
    if keep == 0:
        tail: list[Message] = []
    else:
        # Find index of the keep-th last user message; tail starts there.
        user_idx = [i for i, m in enumerate(rest) if m.role == "user"]
        start = user_idx[-keep] if len(user_idx) >= keep else 0
        # Never orphan a tool reply: if tail starts mid tool sequence, extend
        # backwards to include the assistant turn that owns those tool_calls.
        while start > 0 and rest[start].role == "tool":
            start -= 1
        tail = rest[start:]
    head = rest[: len(rest) - len(tail)] if tail else rest
    # Drop a trailing assistant-with-unresolved-tool-calls from the head so the
    # summary prompt never contains a dangling tool_call.
    while head and head[-1].role == "assistant" and head[-1].tool_calls \
            and not any(m.role == "tool" for m in head):
        head.pop()
    # Also drop dangling tool_calls at the head/tail seam: if head ends with an
    # assistant tool_call but its tool replies fell into the tail start, the
    # head is still self-consistent only if its tool replies are in the head.
    # The tail-backtrack above prevents the split inside a pair, so nothing more
    # to do here.
    if not [m for m in head if m.role in ("user", "assistant", "tool")]:
        return [], list(messages)
    return head, system + tail


def build_summary_messages(head: list[Message], extra: str = "") -> list[Message]:
    lines: list[str] = []
    for m in head:
        body = (m.content or "")
        if len(body) > _SUMMARIZE_TRUNC:
            body = body[:_SUMMARIZE_TRUNC] + f"\n...[truncated {len(m.content or '') - _SUMMARIZE_TRUNC} chars]"
        if m.role == "user":
            lines.append(f"USER: {body}")
        elif m.role == "assistant":
            if m.tool_calls:
                calls = ", ".join(f"{tc.name}({_short_args(tc.arguments)})" for tc in m.tool_calls)
                lines.append(f"ASSISTANT (tools: {calls}): {body}")
            else:
                lines.append(f"ASSISTANT: {body}")
        elif m.role == "tool":
            lines.append(f"TOOL {m.tool_name or ''}: {body}")
    transcript = "\n\n".join(lines) or "(empty)"
    prompt = (
        "Summarize this coding-agent conversation for context compaction. "
        "Keep it dense and actionable under ~1500 words:\n"
        "1. Goal & current state (what was asked, what is done, what is next)\n"
        "2. Key files touched + important code decisions\n"
        "3. Tool results that still matter (paths, versions, URLs, errors)\n"
        "4. Open todos / blockers\n"
        "Do NOT include chitchat. Preserve exact file paths and identifiers."
    )
    if extra.strip():
        prompt += f"\nFocus additionally: {extra.strip()}"
    return [Message(role="user", content=f"{prompt}\n\n--- TRANSCRIPT ---\n{transcript}")]


def _short_args(args: object) -> str:
    try:
        import json as _json
        s = _json.dumps(args, ensure_ascii=False, default=str)
    except Exception:
        s = str(args)
    return s if len(s) <= 160 else s[:160] + "…"


def apply_compaction(messages: list[Message], tail: list[Message], summary: str,
                     model: str = "") -> tuple[list[Message], CompactStats]:
    before = count_messages_tokens(messages, model)
    summary_msg = Message(role="user", content=SUMMARY_PREFIX + summary.strip())
    # tail[0] is system when present; summary goes right after it.
    if tail and tail[0].role == "system":
        new_messages = [tail[0], summary_msg, *tail[1:]]
    else:
        new_messages = [summary_msg, *tail]
    after = count_messages_tokens(new_messages, model)
    kept = sum(1 for m in tail if m.role == "user")
    return new_messages, CompactStats(before, after, kept, len(summary))
