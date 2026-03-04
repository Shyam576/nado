"""
actions.py — PC / computer control functions for the Nado assistant.

Parses <ACTION> tags from Claude's reply, dispatches to the appropriate
handler, and returns a human-readable confirmation string.

Supported action types
───────────────────────
  open_app    → open a named application
  type_text   → type a string via the keyboard
  web_search  → open the default browser with a Google search
  open_url    → open a specific URL
  screenshot  → capture and save a screenshot
  run_command → execute an arbitrary shell command
"""

import json
import logging
import re
import subprocess
import sys
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pyautogui

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex to extract the first <ACTION>…</ACTION> block from a reply
# ---------------------------------------------------------------------------

_ACTION_RE = re.compile(r"<ACTION>(.*?)</ACTION>", re.DOTALL | re.IGNORECASE)

# ---------------------------------------------------------------------------
# Platform-specific app launch helpers
# ---------------------------------------------------------------------------

# Map of friendly app names → macOS bundle names / Windows executable names
_MACOS_APP_MAP: dict[str, str] = {
    "spotify": "Spotify",
    "chrome": "Google Chrome",
    "firefox": "Firefox",
    "safari": "Safari",
    "terminal": "Terminal",
    "finder": "Finder",
    "vscode": "Visual Studio Code",
    "code": "Visual Studio Code",
    "notes": "Notes",
    "calendar": "Calendar",
    "mail": "Mail",
    "slack": "Slack",
    "discord": "Discord",
    "zoom": "zoom.us",
    "word": "Microsoft Word",
    "excel": "Microsoft Excel",
    "powerpoint": "Microsoft PowerPoint",
    "notion": "Notion",
    "obsidian": "Obsidian",
    "whatsapp": "WhatsApp",
    "messages": "Messages",
    "facetime": "FaceTime",
    "photos": "Photos",
    "music": "Music",
    "podcasts": "Podcasts",
    "calculator": "Calculator",
    "activity monitor": "Activity Monitor",
    "system preferences": "System Preferences",
    "system settings": "System Settings",
}

_WINDOWS_APP_MAP: dict[str, str] = {
    "spotify": "spotify",
    "chrome": "chrome",
    "firefox": "firefox",
    "notepad": "notepad",
    "calculator": "calc",
    "explorer": "explorer",
    "vscode": "code",
    "code": "code",
    "slack": "slack",
    "discord": "discord",
    "zoom": "zoom",
    "word": "winword",
    "excel": "excel",
    "powerpoint": "powerpnt",
    "edge": "msedge",
    "paint": "mspaint",
    "task manager": "taskmgr",
    "cmd": "cmd",
    "powershell": "powershell",
}


def _open_app_macos(app_name: str) -> None:
    """Launch a macOS application by its bundle name.

    Args:
        app_name: The display name of the application (e.g. "Spotify").
    """
    resolved = _MACOS_APP_MAP.get(app_name.lower(), app_name)
    subprocess.Popen(["open", "-a", resolved])


def _open_app_windows(app_name: str) -> None:
    """Launch a Windows application by its executable name.

    Args:
        app_name: The friendly name or executable of the application.
    """
    resolved = _WINDOWS_APP_MAP.get(app_name.lower(), app_name)
    subprocess.Popen(["start", resolved], shell=True)


def _open_app_linux(app_name: str) -> None:
    """Launch a Linux application using xdg-open or direct execution.

    Args:
        app_name: The executable or friendly name of the application.
    """
    subprocess.Popen([app_name.lower()])


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------


def open_app(app: str) -> str:
    """Open a desktop application by name.

    Args:
        app: Friendly application name (e.g. "Spotify", "VSCode").

    Returns:
        Confirmation string to be logged.
    """
    logger.info("Action: open_app('%s')", app)
    if sys.platform == "darwin":
        _open_app_macos(app)
    elif sys.platform == "win32":
        _open_app_windows(app)
    else:
        _open_app_linux(app)
    return f"Opened {app}."


def type_text(text: str) -> str:
    """Type a string using the keyboard via PyAutoGUI.

    Adds a small interval between keystrokes for reliability.

    Args:
        text: The string to type.

    Returns:
        Confirmation string to be logged.
    """
    logger.info("Action: type_text (length=%d)", len(text))
    pyautogui.write(text, interval=0.04)
    return f"Typed: {text[:40]}{'…' if len(text) > 40 else ''}"


def web_search(query: str) -> str:
    """Open the default browser with a Google search for the given query.

    Args:
        query: The search query string.

    Returns:
        Confirmation string to be logged.
    """
    logger.info("Action: web_search('%s')", query)
    url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
    webbrowser.open(url)
    return f"Searched for: {query}"


def open_url(url: str) -> str:
    """Open a specific URL in the default browser.

    Args:
        url: A fully qualified URL (e.g. "https://example.com").

    Returns:
        Confirmation string to be logged.
    """
    logger.info("Action: open_url('%s')", url)
    webbrowser.open(url)
    return f"Opened URL: {url}"


def take_screenshot() -> str:
    """Capture the screen and save it to the desktop with a timestamp.

    Returns:
        The absolute path of the saved screenshot as a string.
    """
    logger.info("Action: screenshot")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"nado_screenshot_{timestamp}.png"

    desktop = Path.home() / "Desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    save_path = desktop / filename

    screenshot = pyautogui.screenshot()
    screenshot.save(save_path)
    logger.info("Screenshot saved to %s", save_path)
    return str(save_path)


def run_command(cmd: str) -> str:
    """Execute a shell command and return its stdout (truncated to 200 chars).

    Args:
        cmd: The shell command string to execute.

    Returns:
        The command's stdout (or a short error description on failure).
    """
    logger.info("Action: run_command('%s')", cmd)
    result = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = result.stdout.strip() or result.stderr.strip() or "(no output)"
    truncated = output[:200] + ("…" if len(output) > 200 else "")
    logger.debug("Command output: %s", truncated)
    return truncated


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def parse_and_execute(reply: str) -> tuple[Optional[str], str]:
    """Parse an <ACTION> block from Claude's reply and execute it.

    Args:
        reply: Claude's full reply text, potentially containing an
               ``<ACTION>…</ACTION>`` block.

    Returns:
        A 2-tuple of:
          - action_result: A short confirmation string, or None if no action
            was found or the action failed.
          - clean_reply: The reply with the <ACTION> block removed and
            leading/trailing whitespace stripped.
    """
    match = _ACTION_RE.search(reply)
    clean_reply = _ACTION_RE.sub("", reply).strip()

    if not match:
        return None, clean_reply

    raw_json = match.group(1).strip()

    try:
        action: dict[str, Any] = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse ACTION JSON: %s — %s", raw_json, exc)
        return None, clean_reply

    action_type: str = action.get("type", "")

    try:
        if action_type == "open_app":
            result = open_app(action.get("app", ""))
        elif action_type == "type_text":
            result = type_text(action.get("text", ""))
        elif action_type == "web_search":
            result = web_search(action.get("query", ""))
        elif action_type == "open_url":
            result = open_url(action.get("url", ""))
        elif action_type == "screenshot":
            path = take_screenshot()
            result = f"Screenshot saved to {path}."
        elif action_type == "run_command":
            output = run_command(action.get("cmd", ""))
            result = f"Command output: {output}"
        else:
            logger.warning("Unknown action type: '%s'", action_type)
            result = None
    except Exception as exc:  # noqa: BLE001
        logger.exception("Action '%s' failed: %s", action_type, exc)
        return None, "I couldn't do that, sorry."

    return result, clean_reply
