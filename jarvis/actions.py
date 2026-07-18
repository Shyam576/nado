"""
actions.py — PC / computer control functions for the Jarvis assistant.

Parses <ACTION> tags from the LLM's reply, dispatches to the appropriate
handler, and returns a human-readable confirmation string.

Supported action types
───────────────────────
  open_app          → open a named application
  type_text         → type a string via the keyboard
  web_search        → open the default browser with a Google search
  open_url          → open a specific URL
  screenshot        → capture and save a screenshot
  run_command       → execute an arbitrary shell command
  get_datetime      → return the current date and time
  get_weather       → fetch current weather for a location (Open-Meteo, no key)
  show_notification → display a native desktop notification
  set_reminder      → schedule a spoken/notification reminder after N minutes
  read_clipboard    → read the system clipboard contents
  write_clipboard   → write text to the system clipboard
  play_media        → open a YouTube search in the browser
"""

import json
import logging
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
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
    "camera": "Camera",
    "photo booth": "Photo Booth",
    "maps": "Maps",
    "reminders": "Reminders",
    "contacts": "Contacts",
    "preview": "Preview",
    "quicktime": "QuickTime Player",
    "quicktime player": "QuickTime Player",
    "textedit": "TextEdit",
    "text edit": "TextEdit",
    "pages": "Pages",
    "numbers": "Numbers",
    "keynote": "Keynote",
    "iterm": "iTerm",
    "iterm2": "iTerm2",
    "xcode": "Xcode",
    "simulator": "Simulator",
    "stickies": "Stickies",
    "clock": "Clock",
    "home": "Home",
    "news": "News",
    "stocks": "Stocks",
    "weather": "Weather",
    "shortcuts": "Shortcuts",
    "arc": "Arc",
    "brave": "Brave Browser",
    "opera": "Opera",
    "telegram": "Telegram",
    "signal": "Signal",
    "skype": "Skype",
    "teams": "Microsoft Teams",
    "microsoft teams": "Microsoft Teams",
    "onenote": "Microsoft OneNote",
    "one note": "Microsoft OneNote",
    "outlook": "Microsoft Outlook",
    "figma": "Figma",
    "sublime": "Sublime Text",
    "sublime text": "Sublime Text",
    "pycharm": "PyCharm",
    "webstorm": "WebStorm",
    "intellij": "IntelliJ IDEA",
    "cursor": "Cursor",
    "warp": "Warp",
    "hyper": "Hyper",
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


def _open_app_macos(app_name: str) -> Optional[str]:
    """Launch a macOS application by its bundle name.

    Args:
        app_name: The display name of the application (e.g. "Spotify").

    Returns:
        None on success, or an error description if the app could not be
        opened (e.g. it is not installed).
    """
    resolved = _MACOS_APP_MAP.get(app_name.lower(), app_name)
    result = subprocess.run(
        ["open", "-a", resolved], capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        return result.stderr.strip() or f"Could not open '{app_name}'."
    return None


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
        error = _open_app_macos(app)
        if error:
            logger.warning("open_app failed: %s", error)
            return f"Couldn't open {app} — {error}"
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

    Uses macOS built-in `screencapture` on macOS (handles permissions correctly),
    falls back to pyautogui on other platforms.

    Returns:
        The absolute path of the saved screenshot as a string.
    """
    logger.info("Action: screenshot")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"nado_screenshot_{timestamp}.png"

    desktop = Path.home() / "Desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    save_path = desktop / filename

    if sys.platform == "darwin":
        result = subprocess.run(
            ["screencapture", "-x", str(save_path)],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"screencapture failed: {result.stderr.strip()}")
    else:
        screenshot = pyautogui.screenshot()
        screenshot.save(save_path)

    logger.info("Screenshot saved to %s", save_path)
    return str(save_path)


def run_command(cmd: str, max_chars: int = 200, cwd: Optional[str] = None) -> str:
    """Execute a shell command and return its stdout (truncated).

    Args:
        cmd: The shell command string to execute.
        max_chars: Truncation limit for the output. The 200 default suits the
                   spoken voice pipeline; chat transports pass a larger limit.
        cwd: Working directory for the command. None keeps the process cwd
             (voice-mode behaviour); chat transports pass the home directory
             so relative paths like "Desktop" resolve intuitively.

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
        cwd=cwd,
    )
    output = result.stdout.strip() or result.stderr.strip() or "(no output)"
    truncated = output[:max_chars] + ("…" if len(output) > max_chars else "")
    logger.debug("Command output: %s", truncated)
    return truncated


def get_datetime() -> str:
    """Return the current date and time as a friendly string.

    Returns:
        Human-readable date/time string, e.g. "Tuesday, 10 March 2026 — 14:35".
    """
    logger.info("Action: get_datetime")
    now = datetime.now()
    return now.strftime("%A, %d %B %Y — %H:%M")


def get_weather(location: str) -> str:
    """Fetch current weather for a location using Open-Meteo (free, no API key).

    Performs two HTTP calls: geocoding (Nominatim) → weather (Open-Meteo).

    Args:
        location: City name or location string, e.g. "London" or "New York".

    Returns:
        A short natural-language weather summary, or an error string.
    """
    logger.info("Action: get_weather('%s')", location)

    # Step 1 — geocode the location using Open-Meteo's geocoding API
    try:
        geo_url = (
            "https://geocoding-api.open-meteo.com/v1/search?"
            + urllib.parse.urlencode({"name": location, "count": 1, "language": "en", "format": "json"})
        )
        with urllib.request.urlopen(geo_url, timeout=5) as resp:
            geo_data = json.loads(resp.read().decode())

        results = geo_data.get("results")
        if not results:
            return f"I couldn't find a location called '{location}'."

        lat = results[0]["latitude"]
        lon = results[0]["longitude"]
        place_name = results[0].get("name", location)
    except Exception as exc:
        logger.warning("Geocoding failed: %s", exc)
        return "I couldn't look up that location right now — no internet perhaps?"

    # Step 2 — fetch current weather
    try:
        wx_url = (
            "https://api.open-meteo.com/v1/forecast?"
            + urllib.parse.urlencode({
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
                "wind_speed_unit": "kmh",
                "temperature_unit": "celsius",
                "timezone": "auto",
            })
        )
        with urllib.request.urlopen(wx_url, timeout=5) as resp:
            wx_data = json.loads(resp.read().decode())

        current = wx_data.get("current", {})
        temp = current.get("temperature_2m", "?")
        feels = current.get("apparent_temperature", "?")
        wind = current.get("wind_speed_10m", "?")
        code = current.get("weather_code", 0)
    except Exception as exc:
        logger.warning("Weather fetch failed: %s", exc)
        return "I fetched the coordinates but couldn't pull the weather data. The API may be unreachable."

    condition = _weather_code_to_text(code)
    return (
        f"{place_name}: {condition}, {temp}°C (feels like {feels}°C), "
        f"wind {wind} km/h."
    )


# WMO Weather Code → description mapping (subset of common codes)
_WMO_CODES: dict[int, str] = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "icy fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    61: "light rain", 63: "moderate rain", 65: "heavy rain",
    71: "light snow", 73: "moderate snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "moderate showers", 82: "heavy showers",
    85: "light snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm",
}


def _weather_code_to_text(code: int) -> str:
    return _WMO_CODES.get(code, f"conditions (code {code})")


def show_notification(title: str, message: str) -> str:
    """Display a native desktop notification.

    Uses osascript on macOS, libnotify on Linux, and a PowerShell toast on Windows.

    Args:
        title:   Notification title.
        message: Notification body text.

    Returns:
        Confirmation string.
    """
    logger.info("Action: show_notification('%s')", title)
    try:
        if sys.platform == "darwin":
            script = f'display notification "{message}" with title "{title}"'
            subprocess.run(["osascript", "-e", script], check=True, capture_output=True)
        elif sys.platform == "win32":
            ps_script = (
                f'$ToastTitle = "{title}"; $ToastText = "{message}"; '
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
                "ContentType = WindowsRuntime] > $null; "
                "$Template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
                "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
                "$RawXml = [xml] $Template.GetXml(); "
                "($RawXml.toast.visual.binding.text | Where-Object {$_.id -eq '1'}).AppendChild("
                "$RawXml.CreateTextNode($ToastTitle)) > $null; "
                "($RawXml.toast.visual.binding.text | Where-Object {$_.id -eq '2'}).AppendChild("
                "$RawXml.CreateTextNode($ToastText)) > $null; "
                "$SerializedXml = New-Object Windows.Data.Xml.Dom.XmlDocument; "
                "$SerializedXml.LoadXml($RawXml.GetXml()); "
                "$Toast = [Windows.UI.Notifications.ToastNotification]::new($SerializedXml); "
                "$Toast.Tag = 'Jarvis'; $Toast.Group = 'Jarvis'; "
                "$Notifier = [Windows.UI.Notifications.ToastNotificationManager]::"
                "CreateToastNotifier('Jarvis'); $Notifier.Show($Toast);"
            )
            subprocess.run(["powershell", "-Command", ps_script], check=True, capture_output=True)
        else:
            subprocess.run(["notify-send", title, message], check=True, capture_output=True)
    except Exception as exc:
        logger.warning("Notification failed: %s", exc)
        return f"Notification could not be shown: {exc}"
    return f"Notification shown: {title} — {message}"


def set_reminder(message: str, minutes: int) -> str:
    """Schedule a reminder to fire after a given number of minutes.

    Uses a daemon thread with a sleep so the main loop isn't blocked.
    On expiry, plays a desktop notification and calls speak() if available.

    Args:
        message: The reminder text to deliver.
        minutes: Delay in minutes (clamped to 1–1440).

    Returns:
        An acknowledgement string confirming the reminder was set.
    """
    minutes = max(1, min(int(minutes), 1440))  # sanity clamp: 1 min – 24 h
    logger.info("Action: set_reminder('%s', %d min)", message, minutes)
    delay_seconds = minutes * 60

    def _fire() -> None:
        time.sleep(delay_seconds)
        show_notification("Jarvis Reminder", message)
        try:
            from voice import speak
            speak(f"Reminder: {message}")
        except Exception:  # noqa: BLE001
            pass

    t = threading.Thread(target=_fire, daemon=True, name=f"reminder-{minutes}min")
    t.start()
    return f"Reminder set: '{message}' in {minutes} minute{'s' if minutes != 1 else ''}."


def read_clipboard() -> str:
    """Read and return the current system clipboard contents.

    Returns:
        Clipboard text, or an empty string if the clipboard is empty or inaccessible.
    """
    logger.info("Action: read_clipboard")
    try:
        if sys.platform == "darwin":
            result = subprocess.run(["pbpaste"], capture_output=True, text=True)
            return result.stdout or "(clipboard is empty)"
        elif sys.platform == "win32":
            result = subprocess.run(
                ["powershell", "-command", "Get-Clipboard"],
                capture_output=True, text=True,
            )
            return result.stdout.strip() or "(clipboard is empty)"
        else:
            result = subprocess.run(["xclip", "-selection", "clipboard", "-o"],
                                    capture_output=True, text=True)
            return result.stdout or "(clipboard is empty)"
    except Exception as exc:
        logger.warning("read_clipboard failed: %s", exc)
        return "I couldn't read the clipboard."


def write_clipboard(text: str) -> str:
    """Write text to the system clipboard.

    Args:
        text: The string to copy to the clipboard.

    Returns:
        Confirmation string.
    """
    logger.info("Action: write_clipboard (length=%d)", len(text))
    try:
        if sys.platform == "darwin":
            proc = subprocess.run(["pbcopy"], input=text, text=True, capture_output=True)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr)
        elif sys.platform == "win32":
            subprocess.run(
                ["powershell", "-command", f'Set-Clipboard -Value "{text}"'],
                capture_output=True, text=True, check=True,
            )
        else:
            proc = subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=text, text=True, capture_output=True,
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr)
    except Exception as exc:
        logger.warning("write_clipboard failed: %s", exc)
        return "I couldn't write to the clipboard."
    label = text[:40] + ("…" if len(text) > 40 else "")
    return f"Copied to clipboard: {label}"


def play_media(query: str) -> str:
    """Open a YouTube search for the given query in the default browser.

    Args:
        query: What to search for on YouTube (e.g. "lo-fi music").

    Returns:
        Confirmation string.
    """
    logger.info("Action: play_media('%s')", query)
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query)
    webbrowser.open(url)
    return f"Opened YouTube search for: {query}"


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
        elif action_type == "get_datetime":
            result = get_datetime()
        elif action_type == "get_weather":
            result = get_weather(action.get("location", "your location"))
        elif action_type == "show_notification":
            result = show_notification(
                action.get("title", "Jarvis"),
                action.get("message", ""),
            )
        elif action_type == "set_reminder":
            result = set_reminder(
                action.get("message", "Reminder"),
                action.get("minutes", 5),
            )
        elif action_type == "read_clipboard":
            result = read_clipboard()
        elif action_type == "write_clipboard":
            result = write_clipboard(action.get("text", ""))
        elif action_type == "play_media":
            result = play_media(action.get("query", ""))
        else:
            logger.warning("Unknown action type: '%s'", action_type)
            result = None
    except Exception as exc:  # noqa: BLE001
        logger.exception("Action '%s' failed: %s", action_type, exc)
        return None, "I'm afraid I couldn't complete that action."

    return result, clean_reply
