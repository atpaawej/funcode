"""Web tools: web_fetch (keyless, stdlib) + web_search (TinyFish REST)."""
from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from typing import Any

from ..core.tools import Tool, ToolContext, ToolProvider

_TRUNC = 12_000
_UA = {"User-Agent": "funcode/0.1 (personal agent)"}


def _truncate(s: str, n: int = _TRUNC) -> str:
    return s if len(s) <= n else s[:n] + f"\n...[truncated {len(s) - n} chars]"


def _html_to_text(page: str) -> str:
    page = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", page)
    page = re.sub(r"(?is)<!--.*?-->", " ", page)
    text = re.sub(r"(?is)<[^>]+>", " ", page)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


class WebFetchTool(Tool):
    name = "web_fetch"
    description = "Fetch a URL and return its text content (HTML stripped). No API key needed."
    risk = "read"
    parameters = {"type": "object", "properties": {
        "url": {"type": "string"},
        "max_chars": {"type": "integer", "default": 12000},
    }, "required": ["url"]}

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        url = args["url"]
        if not re.match(r"^https?://", url):
            return "Error: url must start with http:// or https://"
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=25) as r:
                raw = r.read(2_000_000).decode("utf-8", errors="replace")
                ctype = r.headers.get("Content-Type", "")
        except Exception as e:
            return f"Error fetching {url}: {e}"
        text = raw if "text/html" not in ctype else _html_to_text(raw)
        if not text.strip():
            return f"Error: no readable text at {url}"
        return _truncate(text, int(args.get("max_chars", _TRUNC)))


class WebSearchTool(Tool):
    """TinyFish Search REST: GET api.search.tinyfish.ai?query=... + X-API-Key."""

    name = "web_search"
    description = "Search the live web (TinyFish). Returns titles, snippets, URLs."
    risk = "read"
    parameters = {"type": "object", "properties": {
        "query": {"type": "string"},
        "limit": {"type": "integer", "default": 8},
    }, "required": ["query"]}

    def __init__(self, api_key: str = ""):
        self.api_key = api_key

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        if not self.api_key:
            return ("Error: web_search not configured. Get a free key at "
                    "https://agent.tinyfish.ai/api-keys and set web.tinyfish_api_key "
                    "in settings.json (or $TINYFISH_API_KEY).")
        q = urllib.parse.urlencode({"query": args["query"]})
        try:
            req = urllib.request.Request(f"https://api.search.tinyfish.ai?{q}",
                                         headers={"X-API-Key": self.api_key, **_UA})
            with urllib.request.urlopen(req, timeout=25) as r:
                data = json.loads(r.read(1_000_000).decode("utf-8", errors="replace"))
        except Exception as e:
            return f"Error searching '{args['query']}': {e}"
        results = data.get("results", []) if isinstance(data, dict) else []
        if not results:
            return f"(no results for '{args['query']}')"
        lim = max(1, min(int(args.get("limit", 8)), 20))
        lines: list[str] = []
        for i, item in enumerate(results[:lim], 1):
            title = item.get("title", "(no title)")
            url = item.get("url", "")
            snippet = (item.get("snippet", "") or "").strip()[:400]
            lines.append(f"[{i}] {title}\n    {url}\n    {snippet}")
        return "\n\n".join(lines)


class WebProvider(ToolProvider):
    name = "web"

    def __init__(self, tinyfish_api_key: str = ""):
        self._tools = [WebFetchTool(), WebSearchTool(api_key=tinyfish_api_key)]

    def list_tools(self) -> list[Tool]:
        return list(self._tools)
