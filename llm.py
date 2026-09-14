"""Gemini-backed brain for Jarvis: a small tool-calling chat loop.

Uses the `openai` SDK pointed at Google's OpenAI-compatible endpoint rather
than Google's native SDK, so the tool-calling plumbing below is the same
shape either provider would need. Gemini's free tier (250K TPM, shared
across models) was picked over Groq's (8K TPM for gpt-oss-120b) because
Jarvis resends its full ~1.9K-token tool schema set on every single call --
Groq's tier could only sustain 2-4 such calls per minute; Gemini's isn't
close to that bottleneck at normal conversational pace.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import openai
from openai import OpenAI

from tools import call_tool, get_tool_schemas

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
# gemini-3.6-flash's free tier turned out to cap at just 20 requests/day
# (confirmed live against the API, not a docs claim) -- nowhere near enough
# for a tool-calling assistant. gemini-3.5-flash-lite's free tier is
# meaningfully more usable in practice (lite tiers are built for higher
# throughput); verified it still handles tool-calling and multi-turn
# context correctly before making it the default.
MODEL = os.environ.get("JARVIS_MODEL", "gemini-3.5-flash-lite")

# How many user turns of conversation history to keep. Every call resends
# the full history (on top of the ~1.9K-token tool schema set), so letting
# it grow unbounded across a conversation burns through the per-minute token
# budget faster than it needs to -- see llm.py's Jarvis._trim_history.
MAX_HISTORY_TURNS = 6

# How many tool-call round-trips a single ask() can make before giving up.
# 5 was tuned back when most requests were single-tool (get_current_datetime,
# open_url). Desktop automation tasks (find_text_on_screen -> click_at ->
# type_text, plus a describe_screen check) routinely need 4-6 rounds on
# their own, so 5 was cutting real, in-progress tasks off -- that's what
# "I got stuck juggling tools" actually meant, not that something was wrong.
MAX_TOOL_ROUNDS = 10

SYSTEM_PROMPT = (
    "You're Jarvis. Talk like a sharp, laid-back friend texting back — not a "
    "formal assistant, not a butler, never call the user 'sir'. Casual "
    "phrasing, contractions, dry humor. Light slang (fr, ngl, lowkey, no "
    "cap) is fine ONLY when it actually fits naturally — at most one such "
    "word per reply, and skip it entirely most of the time. Forcing slang "
    "into every line reads as trying too hard, which is worse than not "
    "using it at all. Same deal with emoji: rare, at most one per reply, "
    "only when it genuinely adds something — never stack them, never use "
    "them as decoration. No 'as an AI', no corporate hedging, no customer- "
    "support voice. You're being heard (or read as a quick text), so keep "
    "it short — no markdown, no bullet lists, no code blocks. Use tools "
    "whenever a question needs live info instead of guessing. If a tool "
    "call fails, say so plainly rather than making something up.\n\n"
    "Calibration — right: \"yeah it's 3pm, you've got the dentist at 4\". "
    "Also right: \"oof, that sucks, wanna talk about it\". "
    "Wrong (too much): \"OMG bestie it's literally 3pm rn, no cap, time is "
    "FLYING today! \U0001f525\U0001f480\". Slang/emoji tacked onto the end "
    "of a sentence just to have some there is always wrong — only use one "
    "if it's the most natural word for that exact thought.\n\n"
    "Desktop/browser UI tasks (clicking or typing into an app or website via "
    "click_at/type_text/find_text_on_screen/browser_fill_and_submit) are "
    "multi-step by nature — do them one verified step at a time instead of "
    "guessing several moves ahead. E.g. sending an email: find/click "
    "Compose, confirm it opened, find/click the To field, type it, find/"
    "click Subject, type it, find/click the body, type it, find/click Send. "
    "Don't assume a click landed correctly or a field is the right one — "
    "when it matters, check (describe_screen, or look at what "
    "find_text_on_screen returns) before moving to the next step. Going "
    "slower but correct beats a fast wrong guess that has to be undone.\n\n"
    "Before starting a multi-step task like that, make sure you actually "
    "have what you need. \"Send an email\" on its own is missing who it's "
    "to and what it should say -- ask for those first instead of guessing "
    "or starting to click around without them. Same idea for anything "
    "else where a key detail is missing or ambiguous (which account, which "
    "of several open tabs, etc.) -- one quick question beats doing the "
    "wrong thing carefully.\n\n"
    "If a request names an action but not a target specific enough to find "
    "-- \"click the button\" with no label, app, or window named -- that's "
    "exactly the case for one concrete clarifying question up front (\"which "
    "app is this in, and what does the button say or look like?\"), not "
    "repeated screenshotting to guess it out. Never call describe_screen or "
    "click_at more than twice in a row without either landing a verified, "
    "successful click or stopping to ask the user something specific you're "
    "missing -- looping on screenshots instead of acting or asking burns "
    "turns and dumps a pile of near-identical images on the user for "
    "nothing. If a click_at result's `after` shows nothing changed, that's "
    "a signal to try find_text_on_screen for an exact target or ask, not to "
    "retry the same guess or re-describe the same screen again."
)


class Jarvis:
    def __init__(self):
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Copy .env.example to .env and add "
                "your free key from aistudio.google.com/apikey."
            )
        self.client = OpenAI(api_key=api_key, base_url=GEMINI_BASE_URL)
        self.history: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        # Files (e.g. a screenshot from describe_screen) produced by tool calls
        # during the most recent ask(). Front-ends that can show files (like
        # the Telegram bot) can send these; voice-only front-ends can ignore it.
        self.last_attachments: list[str] = []

    def _add_attachment(self, path: str) -> None:
        """Queue a screenshot to send back, but skip it if it's byte-identical
        to one already queued this turn (e.g. describe_screen/click_at called
        repeatedly against an unchanged screen) -- otherwise a stalled tool
        loop floods the user with a pile of the same image instead of one."""
        try:
            new_hash = hashlib.md5(Path(path).read_bytes()).hexdigest()
        except OSError:
            self.last_attachments.append(path)  # can't hash it -- keep it, don't silently drop
            return

        for existing in self.last_attachments:
            try:
                if hashlib.md5(Path(existing).read_bytes()).hexdigest() == new_hash:
                    Path(path).unlink(missing_ok=True)
                    return
            except OSError:
                continue
        self.last_attachments.append(path)

    def _trim_history(self) -> None:
        """Drop the oldest turns once history exceeds MAX_HISTORY_TURNS,
        keeping the system prompt plus the most recent turns. Only cuts at
        user-message boundaries -- an assistant's tool_calls message has to
        stay adjacent to its tool results, or the API rejects the request."""
        user_indices = [i for i, m in enumerate(self.history) if m["role"] == "user"]
        if len(user_indices) <= MAX_HISTORY_TURNS:
            return
        cutoff = user_indices[-MAX_HISTORY_TURNS]
        self.history = [self.history[0], *self.history[cutoff:]]

    def ask(self, user_text: str) -> str:
        self._trim_history()
        history_len_before = len(self.history)
        self.history.append({"role": "user", "content": user_text})
        self.last_attachments = []

        try:
            for _ in range(MAX_TOOL_ROUNDS):
                response = self.client.chat.completions.create(
                    model=MODEL,
                    messages=self.history,
                    tools=get_tool_schemas(),
                    tool_choice="auto",
                )
                message = response.choices[0].message
                self.history.append(message.model_dump(exclude_none=True))

                if not message.tool_calls:
                    return message.content or ""

                for call in message.tool_calls:
                    args = json.loads(call.function.arguments or "{}")
                    result = call_tool(call.function.name, args)
                    if isinstance(result, dict) and "_attachment_path" in result:
                        self._add_attachment(result.pop("_attachment_path"))
                    self.history.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": json.dumps(result),
                        }
                    )

            return "I got stuck juggling tools on that one — try rephrasing?"
        except openai.APIError as exc:
            # Roll back this whole attempt -- don't leave a half-finished turn
            # (a user message with no real reply) sitting in history, since
            # that would silently waste tokens re-sending it on the next ask().
            del self.history[history_len_before:]
            self.last_attachments = []
            return _friendly_api_error(exc)


def _friendly_api_error(exc: openai.APIError) -> str:
    if isinstance(exc, openai.RateLimitError):
        wait = _rate_limit_wait_seconds(exc)
        if wait is not None:
            return f"Hit Gemini's free-tier limit — resets in about {_format_wait(wait)}, try again then."
        return (
            "Hit Gemini's free-tier rate limit — give it a few minutes and "
            "ask again."
        )
    if isinstance(exc, openai.APIConnectionError):
        return "Couldn't reach Gemini's API — check your connection and try again."
    return "The brain's API hiccuped on that one — try again in a bit."


def _rate_limit_wait_seconds(exc: openai.RateLimitError) -> float | None:
    """If the 429 response carries a Retry-After header with the exact
    number of seconds until enough quota frees up, use that instead of
    guessing "a few minutes" -- which can be wildly off in either direction."""
    try:
        return float(exc.response.headers.get("retry-after"))
    except (AttributeError, TypeError, ValueError):
        return None


def _format_wait(seconds: float) -> str:
    seconds = max(1, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}m{secs}s" if secs else f"{minutes}m"
