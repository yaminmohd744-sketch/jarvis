"""List, screenshot, and close tabs in Jarvis's managed Chrome window --
including tabs opened by hand, not just ones Jarvis itself opened."""

from __future__ import annotations

import tempfile
from pathlib import Path

from . import tool


@tool(
    {
        "name": "list_open_tabs",
        "description": "List every tab currently open in Jarvis's Chrome window.",
        "parameters": {"type": "object", "properties": {}},
    }
)
def list_open_tabs() -> dict:
    import browser_session

    tabs = browser_session.list_tabs()
    return {
        "tabs": [
            {"index": i, "title": page.title(), "url": page.url}
            for i, page in enumerate(tabs)
        ]
    }


@tool(
    {
        "name": "screenshot_tab",
        "description": (
            "Screenshot an open tab (index from list_open_tabs, or a "
            "title/URL hint) and queue one image for delivery. Use only when "
            "the user requests an image. A later capture replaces it. Defaults to the "
            "most recently opened tab."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "index": {
                    "type": ["integer", "null"],
                    "description": "From list_open_tabs.",
                },
                "hint": {
                    "type": ["string", "null"],
                    "description": "Matches part of the tab's title/URL.",
                },
            },
        },
    }
)
def screenshot_tab(index: int | None = None, hint: str | None = None) -> dict:
    import browser_session

    page = browser_session.find_tab(index=index, hint=hint)
    if page is None:
        return {"status": "error", "message": "no matching tab is open"}

    screenshot_path = Path(tempfile.mktemp(suffix=".png"))
    page.screenshot(path=str(screenshot_path))
    return {
        "status": "done",
        "title": page.title(),
        "url": page.url,
        "_attachment_path": str(screenshot_path),
    }


@tool(
    {
        "name": "close_tab",
        "description": (
            "Close a tab (index from list_open_tabs, or a title/URL hint). "
            "Defaults to the most recently opened tab."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "index": {
                    "type": ["integer", "null"],
                    "description": "From list_open_tabs.",
                },
                "hint": {
                    "type": ["string", "null"],
                    "description": "Matches part of the tab's title/URL.",
                },
            },
        },
    }
)
def close_tab(index: int | None = None, hint: str | None = None) -> dict:
    import browser_session

    if not browser_session.list_tabs():
        return {"status": "error", "message": "no tabs are currently open"}

    target = browser_session.find_tab(index=index, hint=hint)
    if target is None:
        which = f"index {index}" if index is not None else f"'{hint}'"
        return {"status": "error", "message": f"no open tab matches {which}"}

    title = target.title()
    browser_session.close_tab(target)
    return {"status": "closed", "title": title}


@tool(
    {
        "name": "close_all_tabs",
        "description": "Close every tab currently open in Jarvis's Chrome window.",
        "parameters": {"type": "object", "properties": {}},
    }
)
def close_all_tabs() -> dict:
    import browser_session

    tabs = browser_session.list_tabs()
    count = len(tabs)
    for page in tabs:
        browser_session.close_tab(page)
    return {"status": "closed", "count": count}
