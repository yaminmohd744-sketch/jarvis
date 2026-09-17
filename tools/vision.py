"""Screen vision via Gemini's own vision capability -- same provider/API key
as the main brain (llm.py), not a separate local service.

Replaced a local Ollama + moondream setup: moondream (a tiny ~1.6B local
model) was noticeably weak at reading on-screen text and understanding
context -- e.g. it failed to recognize an active Google Meet call by name
in a live test, while Gemini correctly identified the call, both
participants, and the chat panel's location in the same screenshot. Also
means one less always-running local service (`ollama serve`) to keep alive.
"""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path

from PIL import ImageGrab

from . import tool


def _take_screenshot() -> Path:
    path = Path(tempfile.mktemp(suffix=".png"))
    ImageGrab.grab().save(path, format="PNG")
    return path


@tool(
    {
        "name": "describe_screen",
        "description": (
            "Inspect the screen internally (does not send an image) and describe "
            "it, or answer a specific question about what's currently "
            "visible -- including finding approximate pixel coordinates of "
            "something to click/type into with click_at/type_text. For "
            "anything with actual visible text, find_text_on_screen (OCR) "
            "gives more precise coordinates; use this for non-text targets "
            "(icons, images) or general understanding of what's on screen."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": (
                        "What to look for or ask about the screen. Pass a "
                        "general description request if nothing specific was asked."
                    ),
                }
            },
            "required": ["question"],
        },
    }
)
def describe_screen(question: str = "Describe what's on this screen.") -> dict:
    # Imported lazily (like other tools import browser_session locally) to
    # avoid a circular import: llm.py imports this package at module load
    # time, before its own GEMINI_BASE_URL/MODEL constants would exist yet.
    from llm import GEMINI_BASE_URL, MODEL
    import os
    from openai import OpenAI

    try:
        screenshot_path = _take_screenshot()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"couldn't capture the screen: {exc}"}

    try:
        image_b64 = base64.b64encode(screenshot_path.read_bytes()).decode()
        client = OpenAI(api_key=os.environ["GEMINI_API_KEY"], base_url=GEMINI_BASE_URL)
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": question},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                        },
                    ],
                }
            ],
        )
        description = (response.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001 - surface any vision-call failure plainly
        return {"error": f"couldn't analyze the screenshot: {exc}"}
    finally:
        screenshot_path.unlink(missing_ok=True)

    return {"description": description}


@tool(
    {
        "name": "take_screenshot",
        "description": (
            "Capture the desktop screen and queue one image for delivery to the user. "
            "Use only when the user requests a screenshot, after completing any "
            "requested actions. No analysis or additional capture is needed. "
            "A later capture replaces the queued image."
        ),
        "parameters": {"type": "object", "properties": {}},
    }
)
def take_screenshot() -> dict:
    return {"status": "captured", "_attachment_path": str(_take_screenshot())}
