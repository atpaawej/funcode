"""Per-tool one-line summaries. Pure functions — no Rich, easy to test.

format_call(name, args) -> short label shown on the ⏺ line.
format_result(name, args, output) -> one-line ⎿ summary.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


def _s(v: Any, n: int = 80) -> str:
    s = str(v or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def format_call(name: str, args: dict[str, Any] | None) -> str:
    a = args or {}
    if name == "read":
        p = a.get("path", "…")
        off, lim = a.get("offset", 1), a.get("limit", 200)
        extra = "" if (off in (None, 1) and lim in (None, 200)) else f" {off}:{lim}"
        return f"Read {p}{extra}"
    if name in ("write", "edit"):
        return f"{'Write' if name == 'write' else 'Edit'} {a.get('path', '…')}"
    if name == "bash":
        return f"Bash {_s(a.get('command', '…'), 80)}"
    if name == "glob":
        return f"Glob {_s(a.get('pattern', '…'), 60)}"
    if name == "grep":
        pat = _s(a.get("pattern", "…"), 50)
        inc = a.get("include", "*")
        return f"Grep {pat!r} in {inc}" if inc and inc != "*" else f"Grep {pat!r}"
    if name == "web_fetch":
        try:
            u = urlparse(str(a.get("url", "")))
            short = (u.netloc + u.path)[:60] or str(a.get("url", "…"))
        except Exception:
            short = str(a.get("url", "…"))
        return f"Fetch {short}"
    if name == "web_search":
        return f"Search {_s(a.get('query', '…'), 70)}"
    # generic fallback: name + first arg values, never raw JSON
    vals = [str(v)[:40] for k, v in list(a.items())[:2]]
    return f"{name} {' '.join(vals)}".strip()


def format_result(name: str, args: dict[str, Any] | None, output: str) -> tuple[str, bool]:
    """Return (summary, is_error). Single line, ~120ch max."""
    out = output or ""
    if out.startswith("Error") or out.startswith("Blocked:"):
        return _s(out.splitlines()[0] if out.splitlines() else out, 120), True
    lines = out.splitlines()
    n = len(lines)
    if name == "read":
        return f"{n} lines", False
    if name == "glob":
        if out.strip() == "(no matches)":
            return "no matches", False
        return f"{n} file{'s' if n != 1 else ''}", False
    if name == "grep":
        if "(no matches)" in out:
            return "no matches", False
        # grep tool caps at limit; trailing ... line is not a match
        m = sum(1 for l in lines if l and not l.startswith("..."))
        return f"{m} match{'es' if m != 1 else ''}", False
    if name == "bash":
        # first line is "$ cmd", second is "(exit N)"
        exit_code = "?"
        for l in lines[:3]:
            if l.startswith("(exit"):
                exit_code = l.strip("()").replace("exit ", "")
        body = max(0, n - 2)
        if exit_code not in ("0", "?"):
            first = lines[2] if n > 2 else out
            return f"exit {exit_code} · {_s(first, 90)}", True
        return f"exit {exit_code} · {body} lines", False
    if name == "web_search":
        if out.startswith("(no results"):
            return "no results", False
        # results are "[i] title\n    url\n    snippet" blocks separated by blank
        count = out.count("\n\n") + 1 if out.strip() else 0
        return f"{count} result{'s' if count != 1 else ''}", False
    if name == "web_fetch":
        return f"{len(out)} chars", False
    if name in ("write", "edit"):
        return _s(lines[0] if lines else out, 100), False
    # generic: line count
    if n <= 1:
        return _s(out, 100), False
    return f"{n} lines", False
