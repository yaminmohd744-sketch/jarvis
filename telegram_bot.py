"""Telegram front-end for Jarvis — chat with it from your phone, for free.

Runs via long-polling (Telegram's servers are polled by this script), so
there's no port-forwarding, public IP, or paid tunnel needed. The PC running
this script just needs to be on and connected to the internet.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import os

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, ContextTypes, MessageHandler, filters

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import audio
from llm import Jarvis

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ALLOWED_CHAT_ID = os.environ.get("TELEGRAM_ALLOWED_CHAT_ID")

# One conversation history per Telegram chat, so multiple chats don't bleed
# into each other's context.
_sessions: dict[int, Jarvis] = {}
# Playwright's synchronous objects must stay on their owning thread.
_jarvis_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jarvis")

# Updates are processed one at a time per chat (concurrent_updates is off by
# default -- confirmed, not assumed), so there's no actual race between
# messages. But a real task can now take 20-40s+ (click_at/type_text each
# run a Gemini vision verification call), and with zero acknowledgment that
# a message even arrived, the natural reaction is "did that go through?" ->
# resend -> resend again. Those pile up and then run back-to-back with no
# confirmation in between, which *looks* like chaos even though it's
# strictly sequential. Tracking busy-per-chat lets the immediate ack say
# what's actually happening instead of just going quiet.
_busy: dict[int, bool] = {}


def _get_jarvis(chat_id: int) -> Jarvis:
    if chat_id not in _sessions:
        _sessions[chat_id] = Jarvis()
    return _sessions[chat_id]


async def _handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:  # noqa: ARG001
    message = update.message
    if message is None:
        return
    chat_id = update.effective_chat.id
    print(f"Message from chat id: {chat_id}", flush=True)

    if ALLOWED_CHAT_ID and str(chat_id) != str(ALLOWED_CHAT_ID):
        await message.reply_text(
            f"Not authorized. Your chat id is {chat_id} — add it to .env as "
            f"TELEGRAM_ALLOWED_CHAT_ID and restart the bot."
        )
        return

    if message.voice:
        voice_file = await message.voice.get_file()
        ogg_path = Path(tempfile.mktemp(suffix=".ogg"))
        await voice_file.download_to_drive(str(ogg_path))
        try:
            # transcribe() is a blocking CPU call; run off the event loop.
            text = await asyncio.to_thread(audio.transcribe, str(ogg_path))
        finally:
            ogg_path.unlink(missing_ok=True)
    else:
        text = (message.text or "").strip()

    if not text:
        await message.reply_text("(didn't catch any words in that)")
        return

    # Immediate ack so a slow task never reads as "didn't go through" --
    # that's what was causing repeat sends piling up. Distinguishes a fresh
    # request from one that's now queued behind one still running.
    if _busy.get(chat_id):
        await message.reply_text("still on your last one -- this'll go right after")
    else:
        await message.reply_text("on it, one sec")
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    _busy[chat_id] = True
    try:
        jarvis = _get_jarvis(chat_id)
        # jarvis.ask() is synchronous and can call Playwright's *sync* API
        # (via the browser tools), which refuses to run inside a thread that
        # already has an asyncio event loop -- this handler is exactly that
        # thread. Run it in a plain worker thread (no event loop of its own)
        # instead.
        reply = await asyncio.get_running_loop().run_in_executor(_jarvis_worker, jarvis.ask, text)
    finally:
        _busy[chat_id] = False
    try:
        await message.reply_text(reply)
        for attachment_path in jarvis.last_attachments:
            with open(attachment_path, "rb") as f:
                await message.reply_photo(f)
    finally:
        for attachment_path in jarvis.last_attachments:
            Path(attachment_path).unlink(missing_ok=True)
        jarvis.last_attachments = []


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN is not set. Message @BotFather on Telegram to "
            "create a free bot and get a token, then add it to .env."
        )
    if not ALLOWED_CHAT_ID:
        print(
            "WARNING: TELEGRAM_ALLOWED_CHAT_ID is not set — anyone who finds "
            "this bot could use it. Message it once to learn your chat id, "
            "then add it to .env and restart.",
            file=sys.stderr,
        )

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT | filters.VOICE, _handle))
    print("Jarvis Telegram bot is running. Message your bot to talk to it. (Ctrl+C to stop)")
    try:
        app.run_polling()
    finally:
        _jarvis_worker.shutdown(wait=True)


if __name__ == "__main__":
    main()
