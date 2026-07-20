"""
modules/system.py — Local machine commands: stats dashboard, screen lock,
Downloads cleanup.

These act on the laptop the bot process runs on (same trust model as the
laptop actions in actions.py — the transport allowlists are the auth
boundary).
"""

import datetime
import logging
import subprocess
import sys
from pathlib import Path

import psutil

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# /system — machine stats dashboard
# ---------------------------------------------------------------------------


def _battery_line() -> str:
    battery = psutil.sensors_battery()
    if battery is None:
        return "Battery: none (desktop?)"
    state = "charging" if battery.power_plugged else "on battery"
    line = f"Battery: {battery.percent:.0f}% ({state})"
    if not battery.power_plugged and battery.secsleft not in (
        psutil.POWER_TIME_UNKNOWN,
        psutil.POWER_TIME_UNLIMITED,
    ):
        hours, remainder = divmod(battery.secsleft, 3600)
        line += f", ~{hours}h {remainder // 60}m left"
    return line


def system_status(chat_id: str = "", args: list[str] | None = None) -> str:
    """Return a snapshot of CPU, memory, disk, battery, and uptime.

    Args:
        chat_id: Unused — stats are machine-global.
        args: Unused — kept for a consistent command-handler signature.

    Returns:
        A multi-line plain-text dashboard.
    """
    # Prime per-process CPU counters, then let the 0.5s system-wide sample
    # double as their measurement window (first cpu_percent call always
    # reports 0.0 — psutil needs two samples).
    procs = list(psutil.process_iter(["name"]))
    for proc in procs:
        try:
            proc.cpu_percent()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    cpu_percent = psutil.cpu_percent(interval=0.5)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    boot = datetime.datetime.fromtimestamp(psutil.boot_time())
    uptime = datetime.datetime.now() - boot
    days, remainder = divmod(int(uptime.total_seconds()), 86400)
    hours, remainder = divmod(remainder, 3600)

    lines = [
        f"CPU: {cpu_percent:.0f}%  (load {', '.join(f'{x:.1f}' for x in psutil.getloadavg())})",
        f"RAM: {memory.used / 1e9:.1f} / {memory.total / 1e9:.0f} GB ({memory.percent:.0f}%)",
        f"Disk: {disk.used / 1e9:.0f} / {disk.total / 1e9:.0f} GB ({disk.percent:.0f}%)",
        _battery_line(),
        f"Uptime: {days}d {hours}h {remainder // 60}m",
    ]

    # Top 3 CPU-hungry processes — a quick "what's eating my machine" answer
    usage: list[tuple[float, str]] = []
    for proc in procs:
        try:
            percent = proc.cpu_percent()  # measured over the same 0.5s window
            name = proc.info["name"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if name and percent > 0:
            usage.append((percent, name))
    usage.sort(reverse=True)
    if usage:
        hot = ", ".join(f"{name} ({percent:.0f}%)" for percent, name in usage[:3])
        lines.append(f"Hottest: {hot}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# /lock — lock the screen
# ---------------------------------------------------------------------------


def lock_screen(chat_id: str = "", args: list[str] | None = None) -> str:
    """Lock the machine (sleep displays; password required on wake).

    Args:
        chat_id: Unused.
        args: Unused — kept for a consistent command-handler signature.

    Returns:
        A confirmation or error string.
    """
    if sys.platform != "darwin":
        return "Lock is only wired up for macOS right now."

    result = subprocess.run(
        ["pmset", "displaysleepnow"], capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        return f"Couldn't lock the screen: {result.stderr.strip()}"
    return "Screen locked."


# ---------------------------------------------------------------------------
# /cleanup — organise ~/Downloads into subfolders (move-only, never deletes)
# ---------------------------------------------------------------------------

_CLEANUP_CATEGORIES: dict[str, set[str]] = {
    "PDFs": {".pdf"},
    "Images": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".svg", ".bmp", ".tiff"},
    "Archives": {".zip", ".tar", ".gz", ".tgz", ".rar", ".7z", ".dmg", ".pkg"},
    "Docs": {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".txt", ".md", ".rtf"},
    "Media": {".mp4", ".mov", ".mp3", ".wav", ".m4a", ".mkv", ".avi"},
    "Installers": {".app", ".exe", ".msi", ".deb", ".rpm"},
    "Data": {".json", ".xml", ".sql", ".yaml", ".yml", ".log"},
}

# In-progress downloads must never be touched
_CLEANUP_SKIP_SUFFIXES = {".crdownload", ".download", ".part", ".partial", ".tmp"}


def _unique_destination(folder: Path, name: str) -> Path:
    """Return a collision-free path inside folder for the given filename."""
    destination = folder / name
    if not destination.exists():
        return destination
    stem, suffix = Path(name).stem, Path(name).suffix
    counter = 1
    while destination.exists():
        destination = folder / f"{stem} ({counter}){suffix}"
        counter += 1
    return destination


def cleanup_downloads(chat_id: str = "", args: list[str] | None = None) -> str:
    """Sort loose files in ~/Downloads into category subfolders.

    Move-only and scoped entirely inside ~/Downloads: nothing is deleted,
    directories are left alone, hidden and in-progress files are skipped,
    and name collisions get a " (n)" suffix.

    Args:
        chat_id: Unused.
        args: Unused — kept for a consistent command-handler signature.

    Returns:
        A per-category summary of what was moved.
    """
    downloads = Path.home() / "Downloads"
    if not downloads.is_dir():
        return "No ~/Downloads folder found."

    category_names = set(_CLEANUP_CATEGORIES)
    moved: dict[str, int] = {}
    skipped = 0

    for entry in downloads.iterdir():
        if not entry.is_file() or entry.name.startswith("."):
            continue
        if entry.suffix.lower() in _CLEANUP_SKIP_SUFFIXES:
            skipped += 1
            continue
        if entry.parent.name in category_names:
            continue  # already sorted (shouldn't happen at top level, belt & braces)

        category = next(
            (name for name, exts in _CLEANUP_CATEGORIES.items() if entry.suffix.lower() in exts),
            "Other",
        )
        target_dir = downloads / category
        target_dir.mkdir(exist_ok=True)
        try:
            entry.rename(_unique_destination(target_dir, entry.name))
            moved[category] = moved.get(category, 0) + 1
        except OSError as exc:
            logger.warning("cleanup: could not move %s: %s", entry.name, exc)
            skipped += 1

    if not moved:
        return "Downloads is already tidy — nothing to move."

    total = sum(moved.values())
    lines = [f"Organised {total} file{'s' if total != 1 else ''} in ~/Downloads:"]
    for category, count in sorted(moved.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {category}: {count}")
    if skipped:
        lines.append(f"  (skipped {skipped} in-progress/unmovable)")
    return "\n".join(lines)
