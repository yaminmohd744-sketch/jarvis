"""Basic browser automation via Playwright (free, open-source).

Uses Jarvis's one Google Chrome window (browser_session), connected to the
user's real, already-running Chrome via its remote-debugging port -- so the
user can see and take over what Jarvis is doing, whatever's already logged
in works, and the opened tab stays around afterward — manage it with
list_open_tabs / close_tab.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from . import tool


@tool(
    {
        "name": "browser_fill_and_submit",
        "description": (
            "Fill one form field (by visible label/placeholder) and "
            "optionally submit it, in a NEW tab (pass url) or an EXISTING "
            "tab (pass tab_index/tab_hint instead) -- any open tab, "
            "including ones opened by hand. Returns page title and URL; its "
            "internal screenshot is not delivered to the user. Use screenshot_tab "
            "afterward only if the user requested an image. Tab stays "
            "open -- use close_tab when done. Not for general web search "
            "(search engines block automated browsers) -- use web_search "
            "instead; this only works reliably on ordinary sites/forms "
            "without bot-detection (e.g. Wikipedia)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": ["string", "null"],
                    "description": "URL to open in a new tab. Omit to act on an existing tab instead.",
                },
                "tab_index": {
                    "type": ["integer", "null"],
                    "description": "Existing tab's index from list_open_tabs, instead of url.",
                },
                "tab_hint": {
                    "type": ["string", "null"],
                    "description": "Existing tab's title/URL hint, instead of url.",
                },
                "field_hint": {
                    "type": "string",
                    "description": "Field's visible label, placeholder, or name, e.g. 'Search' or 'Email'.",
                },
                "text": {"type": "string", "description": "Text to type into the field."},
                "submit": {
                    "type": ["boolean", "null"],
                    "description": "Press Enter after typing (default true).",
                },
            },
            "required": ["field_hint", "text"],
        },
    }
)
def browser_fill_and_submit(
    field_hint: str,
    text: str,
    url: str | None = None,
    tab_index: int | None = None,
    tab_hint: str | None = None,
    submit: bool = True,
) -> dict:
    import browser_session

    if tab_index is not None or tab_hint is not None:
        page = browser_session.find_tab(index=tab_index, hint=tab_hint)
        if page is None:
            which = f"index {tab_index}" if tab_index is not None else f"'{tab_hint}'"
            return {"status": "error", "message": f"no open tab matches {which}"}
        if url:
            page.goto(url, wait_until="domcontentloaded")
    elif url:
        page = browser_session.open_tab(url)
    else:
        return {"status": "error", "message": "need either a url or a tab_index/tab_hint"}

    locator = page.get_by_label(field_hint)
    if locator.count() == 0:
        locator = page.get_by_placeholder(field_hint)
    if locator.count() == 0:
        locator = page.locator(f'[name="{field_hint}"]')
    if locator.count() == 0:
        return {
            "status": "error",
            "message": f"couldn't find a field matching '{field_hint}' on {url}",
        }

    locator.first.fill(text)
    if submit:
        locator.first.press("Enter")
        # Wait for the page to actually settle before screenshotting —
        # domcontentloaded fires before results render on most sites;
        # networkidle is a much stronger signal. Bounded so a page with
        # persistent connections (analytics, websockets) can't hang us.
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:  # noqa: BLE001 - timeout is fine, just proceed
            pass

    screenshot_path = Path(tempfile.mktemp(suffix=".png"))
    page.screenshot(path=str(screenshot_path))

    return {
        "status": "done",
        "page_title": page.title(),
        "final_url": page.url,
        "_attachment_path": str(screenshot_path),
    }
