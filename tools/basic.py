"""First-pass tools: clock, launching apps/URLs, local file search."""

from __future__ import annotations

import datetime
import os
import subprocess
import time
from pathlib import Path

from . import tool

# Common spoken app names -> the command Windows can actually launch.
# Anything not in this map is passed straight to `os.startfile`, which also
# resolves plain .exe names found on PATH or registered under App Paths.
_APP_ALIASES = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "paint": "mspaint.exe",
    "task manager": "taskmgr.exe",
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "edge": "msedge.exe",
    "spotify": "spotify.exe",
    "vscode": "code.exe",
    "vs code": "code.exe",
    "visual studio code": "code.exe",
}

# Some apps' actual running process differs from their launch command --
# most notably Windows 11's Calculator: `calc.exe` is a legacy launcher
# stub that redirects to the real Store-packaged process, CalculatorApp.exe.
# taskkill needs the real process name, not the launch command.
_CLOSE_PROCESS_NAMES = {
    "calculator": "CalculatorApp.exe",
    "calc": "CalculatorApp.exe",
}


@tool(
    {
        "name": "get_current_datetime",
        "description": "Get the current local date and time.",
        "parameters": {"type": "object", "properties": {}},
    }
)
def get_current_datetime() -> dict:
    now = datetime.datetime.now()
    return {
        "iso": now.isoformat(timespec="seconds"),
        "day_of_week": now.strftime("%A"),
        "date": now.strftime("%B %d, %Y"),
        "time": now.strftime("%I:%M %p"),
    }


@tool(
    {
        "name": "open_application",
        "description": (
            "Open a desktop application by name, e.g. 'notepad', 'calculator', "
            "'chrome', 'file explorer'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The application's common name.",
                }
            },
            "required": ["name"],
        },
    }
)
def open_application(name: str) -> dict:
    command = _APP_ALIASES.get(name.strip().lower(), name)
    try:
        os.startfile(command)  # noqa: S606 - user-invoked, local desktop app launch
        return {"status": "opened", "app": command}
    except OSError as exc:
        return {"status": "error", "app": command, "error": str(exc)}


def _is_process_running(exe_name: str) -> bool | None:
    """True/False if we could check, None if the check itself failed (so the
    caller doesn't mistake 'couldn't verify' for 'confirmed closed')."""
    check = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {exe_name}"],
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        return None
    return exe_name.lower() in check.stdout.lower()


@tool(
    {
        "name": "close_application",
        "description": (
            "Close a running desktop app by name, e.g. 'notepad'. NOT for "
            "Chrome -- use close_tab/close_all_tabs instead. Tries a normal "
            "close first (lets the app prompt to save); force=true skips "
            "that and can lose unsaved work, so only use it if a normal "
            "close doesn't work."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The application's common name.",
                },
                "force": {
                    "type": ["boolean", "null"],
                    "description": (
                        "Force-kill instead of a normal close (default false)."
                    ),
                },
            },
            "required": ["name"],
        },
    }
)
def close_application(name: str, force: bool = False) -> dict:
    key = name.strip().lower()

    # taskkill matches by image name across the WHOLE machine -- for Chrome
    # that means EVERY window, and since browser_session.py now connects to
    # the user's real, already-running Chrome (not a separate isolated
    # instance), force-killing it this way would kill everything: all the
    # user's own tabs, not just Jarvis's. Too risky to allow; close_tab /
    # close_all_tabs are the correct, precise way to close Jarvis's own tabs.
    if key in ("chrome", "google chrome"):
        return {
            "status": "error",
            "message": (
                "Won't close Chrome this way -- taskkill would hit every "
                "Chrome window on the machine, including ones unrelated to "
                "Jarvis. Use close_tab or close_all_tabs instead."
            ),
        }

    exe_name = _CLOSE_PROCESS_NAMES.get(key)
    if exe_name is None:
        command = _APP_ALIASES.get(key, name)
        exe_name = command if command.lower().endswith(".exe") else f"{command}.exe"

    args = ["taskkill", "/IM", exe_name]
    if force:
        args.append("/F")
    result = subprocess.run(args, capture_output=True, text=True)

    if result.returncode != 0:
        return {
            "status": "error",
            "app": exe_name,
            "message": (result.stderr or result.stdout).strip(),
        }

    if force:
        return {"status": "closed", "app": exe_name, "forced": True}

    # taskkill returns success as soon as the close is *requested* -- some
    # apps (Windows Store/UWP-packaged ones especially, e.g. the Windows 11
    # Calculator) ignore a plain close and keep running. Poll briefly rather
    # than a single fixed-delay check, since a still-closing app (many tabs,
    # autosave, etc.) can legitimately take longer than one short sleep.
    still_running = True
    for _ in range(5):  # ~2s total
        time.sleep(0.4)
        running = _is_process_running(exe_name)
        if running is None:
            return {
                "status": "unknown",
                "app": exe_name,
                "message": "close was requested but couldn't verify whether it actually closed",
            }
        if not running:
            still_running = False
            break

    if still_running:
        return {
            "status": "still_running",
            "app": exe_name,
            "message": (
                "close was requested but the app is still running "
                "(common for Windows Store/UWP apps) -- retry with force=true"
            ),
        }

    return {"status": "closed", "app": exe_name, "forced": False}


# Processes that must never be targeted by close_all_applications -- either
# they ARE the desktop/OS itself (closing them would break the whole
# session), they're Jarvis's own process (closing itself mid-command would
# be, at best, useless, and at worst leave things half-done), or -- chrome.exe
# specifically -- it's the user's REAL browser now (browser_session.py
# connects to their actual running Chrome, not an isolated copy), so a blunt
# WM_CLOSE-everything pass could take down every tab, every login, an
# active call, all of it. close_tab/close_all_tabs are the precise way to
# close Jarvis's own tabs instead.
_NEVER_CLOSE_PROCESSES = {
    "explorer.exe",
    "dwm.exe",
    "svchost.exe",
    "system",
    "csrss.exe",
    "winlogon.exe",
    "wininit.exe",
    "ctfmon.exe",
    "searchhost.exe",
    "searchapp.exe",
    "shellexperiencehost.exe",
    "startmenuexperiencehost.exe",
    "textinputhost.exe",
    "lockapp.exe",
    "python.exe",
    "pythonw.exe",
    "wscript.exe",
    "conhost.exe",
    "cmd.exe",
    "powershell.exe",
    "windowsterminal.exe",
    "chrome.exe",
}


@tool(
    {
        "name": "close_all_applications",
        "description": (
            "Close every visible app window (not Chrome tabs -- use "
            "close_all_tabs). Sends a normal close request to each, so "
            "unsaved work can prompt to save rather than being force-killed. "
            "Never touches system-critical processes or Jarvis itself."
        ),
        "parameters": {"type": "object", "properties": {}},
    }
)
def close_all_applications() -> dict:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    WM_CLOSE = 0x0010
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    closed: list[dict] = []
    skipped = 0

    def _process_name(pid: int) -> str:
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(260)
            size = wintypes.DWORD(260)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return buf.value.rsplit("\\", 1)[-1].lower()
            return ""
        finally:
            kernel32.CloseHandle(handle)

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _enum_callback(hwnd, _lparam):
        nonlocal skipped
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True  # no title -> not a real app window

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc_name = _process_name(pid.value)
        if not proc_name or proc_name in _NEVER_CLOSE_PROCESSES:
            skipped += 1
            return True

        title_buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title_buf, length + 1)
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        closed.append({"title": title_buf.value, "process": proc_name})
        return True

    user32.EnumWindows(_enum_callback, 0)
    return {"status": "done", "closed": closed, "protected_windows_skipped": skipped}


@tool(
    {
        "name": "open_url",
        "description": (
            "Open a URL in a new tab of Jarvis's managed Google Chrome window "
            "(not the system's default browser). Tabs stay open across "
            "requests — use list_open_tabs / close_tab to manage them."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to open."}
            },
            "required": ["url"],
        },
    }
)
def open_url(url: str) -> dict:
    import browser_session

    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    page = browser_session.open_tab(url)
    return {"status": "opened", "url": url, "page_title": page.title()}


_DEFAULT_SEARCH_ROOTS = [
    Path.home() / "Downloads",
    Path.home() / "Documents",
    Path.home() / "Desktop",
]


@tool(
    {
        "name": "search_files",
        "description": (
            "Search for files by (partial) name under the user's common folders "
            "(Downloads, Documents, Desktop)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Filename or partial filename to search for.",
                },
                "max_results": {
                    "type": ["integer", "null"],
                    "description": "Maximum number of matches to return (default 10).",
                },
            },
            "required": ["query"],
        },
    }
)
def search_files(query: str, max_results: int = 10) -> dict:
    query_lower = query.lower()
    matches: list[str] = []
    for root in _DEFAULT_SEARCH_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and query_lower in path.name.lower():
                matches.append(str(path))
                if len(matches) >= max_results:
                    return {"matches": matches}
    return {"matches": matches}
