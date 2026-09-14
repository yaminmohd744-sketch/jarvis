"""Free web search (no API key) via DDGS (formerly duckduckgo-search)."""

from __future__ import annotations

import time

from ddgs import DDGS
from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException

from . import tool


@tool(
    {
        "name": "web_search",
        "description": "Search the web for current information not otherwise known.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."},
                "max_results": {
                    "type": ["integer", "null"],
                    "description": "Maximum number of results to return (default 5).",
                },
            },
            "required": ["query"],
        },
    }
)
def web_search(query: str, max_results: int = 5) -> dict:
    # DDGS is an unofficial scraper around DuckDuckGo, not a stable API -- it
    # intermittently rate-limits or times out with no error handling of its
    # own. One retry after a brief pause clears most of those transient
    # hiccups; a real failure still reports back clearly instead of a raw
    # traceback the model has no way to act on.
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
            return {
                "results": [
                    {
                        "title": r.get("title"),
                        "snippet": r.get("body"),
                        "url": r.get("href"),
                    }
                    for r in results
                ]
            }
        except (RatelimitException, TimeoutException, DDGSException) as exc:
            last_exc = exc
            if attempt == 0:
                time.sleep(1.5)

    return {
        "status": "error",
        "message": f"web search failed after retrying: {last_exc}",
    }
