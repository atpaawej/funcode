"""Pure completion helpers. No prompt_toolkit import here on purpose —
everything in this file is unit-testable without a terminal.

Ordering rule (deterministic, documented): (match_score, name).
  0 = startswith, 1 = substring, 2 = subsequence (fuzzy). No match = dropped.
Source is display-only (shown in menu meta), never affects order.
"""
from __future__ import annotations

import re

_SLASH_RE = re.compile(r"^/([\w\-]*)$")


def _score(name: str, frag: str) -> int | None:
    if not frag:
        return 0
    if name.startswith(frag):
        return 0
    if frag in name:
        return 1
    # subsequence (fuzzy): all frag chars appear in order.
    it = iter(name)
    if all(ch in it for ch in frag):
        return 2
    return None


def match_commands(items: list[tuple[str, str]], frag: str) -> list[str]:
    """Filter (name, source) pairs by frag. Returns names, best first.

    `frag` is the text after `/`, case-insensitive. Empty frag returns all
    names sorted. `items` may contain duplicates — first occurrence wins.
    """
    f = frag.lower()
    seen: set[str] = set()
    scored: list[tuple[int, str]] = []
    for name, _source in items:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        s = _score(key, f)
        if s is not None:
            scored.append((s, key))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [n for _, n in scored]


def exact_command(text: str, names: set[str]) -> str | None:
    """If `text` is exactly `/known-command`, return its canonical name.

    The anti-Claude-#19107 rule: an exact typed match must beat whatever
    the menu has highlighted. Returns None for anything else (args present,
    unknown command, not a slash input).
    """
    m = _SLASH_RE.match(text.strip())
    if not m:
        return None
    key = m.group(1).lower()
    return key if key in names else None
