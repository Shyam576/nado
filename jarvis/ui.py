"""
ui.py - Full-screen terminal TUI for Nado voice mode.

Uses Rich Live display with a self-rendering class (_NadoUI.__rich__) so
that auto_refresh=True re-renders the layout on every tick with zero manual
threading. All public functions are safe no-ops when the UI is not running.
"""

import logging
import threading
from collections import deque
from typing import Optional

from rich import box
from rich.align import Align
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

# Shared state
_active: bool = False
_messages: deque = deque(maxlen=200)
_state: str = "listening"
_level: float = 0.0
_threshold: float = 300.0
_tick: int = 0
_lock = threading.Lock()
_live: Optional[Live] = None
_original_log_handlers: list = []

_BAR_WIDTH = 28
_SPINNER = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]

_STATES = {
    "listening":    ("🎤", "green",         "LISTENING"),
    "recording":    ("🎤", "bright_red",    "● REC"),
    "transcribing": ("💬", "yellow",        "TRANSCRIBING"),
    "thinking":     ("🧠", "cyan",          "THINKING"),
    "speaking":     ("🔊", "bright_blue",   "SPEAKING"),
    "starting":     ("⚡",     "bright_yellow", "STARTING UP"),
    "idle":         ("💤", "dim white",     "IDLE"),
}

# ---------------------------------------------------------------------------
# ASCII avatar
# ---------------------------------------------------------------------------

_AW = 11  # all art lines padded to this width


def _pad_av(lines: list) -> list:
    return [ln.ljust(_AW)[:_AW] for ln in lines]


_ABODY = _pad_av(["   ─┼─   ", "  ╱ │ ╲  ", " ╱  │  ╲ "])


def _ahead(eye: str, mth: str) -> list:
    return _pad_av([" ┌──────┐", eye, mth, " └──────┘"]) + _ABODY


# eye variants
_AV_EYE_OPEN  = " │ o  o │"
_AV_EYE_WIDE  = " │ O  O │"
_AV_EYE_BLINK = " │ ─  ─ │"
_AV_EYE_UP    = " │ ^  ^ │"

# mouth variants
_AV_MTH_FLAT  = " │  ──  │"
_AV_MTH_SM    = " │  ()  │"
_AV_MTH_OPEN  = " │  (o) │"
_AV_MTH_BIG   = " │  (O) │"
_AV_MTH_D     = [" │  .   │", " │  ..  │", " │  ... │", " │  ..  │"]


def _av_frame(state: str, tick: int) -> list:
    """Return 7 lines of padded ASCII art for the current state."""
    if state == "speaking":
        cycle = [_AV_MTH_FLAT, _AV_MTH_SM, _AV_MTH_OPEN,
                 _AV_MTH_BIG, _AV_MTH_OPEN, _AV_MTH_SM]
        return _ahead(_AV_EYE_OPEN, cycle[(tick // 2) % len(cycle)])
    if state in ("thinking", "transcribing"):
        return _ahead(_AV_EYE_UP, _AV_MTH_D[(tick // 6) % len(_AV_MTH_D)])
    if state == "recording":
        return _ahead(_AV_EYE_WIDE, _AV_MTH_FLAT)
    blink = (tick % 40) >= 38
    return _ahead(_AV_EYE_BLINK if blink else _AV_EYE_OPEN, _AV_MTH_FLAT)


class _NadoUI:
    def __rich__(self) -> Layout:
        global _tick
        _tick = (_tick + 1) % len(_SPINNER)
        spinner = _SPINNER[_tick]

        with _lock:
            msgs      = list(_messages)
            state     = _state
            level     = _level
            threshold = max(_threshold, 1.0)

        header = Panel(
            Align.center(
                Text.assemble(
                    ("\n  N · A · D · O  \n", "bold cyan"),
                    ("  Your AI Assistant  ",                "dim white"),
                )
            ),
            box=box.DOUBLE_EDGE,
            border_style="cyan",
        )

        # Only render the last 6 messages so the panel never overflows
        MAX_VISIBLE = 6
        hidden = max(0, len(msgs) - MAX_VISIBLE)
        visible = msgs[-MAX_VISIBLE:] if msgs else []

        body = Text()
        if hidden:
            body.append(f"  ↑ {hidden} earlier message{'s' if hidden > 1 else ''} above\n\n",
                        style="dim italic")
        if visible:
            for i, (role, text) in enumerate(visible):
                if i:
                    body.append("\n\n")
                if role == "user":
                    body.append("  You  ›  ", style="bold bright_white")
                    body.append(text,          style="white")
                else:
                    body.append("  Nado ›  ", style="bold cyan")
                    body.append(text,          style="bright_cyan")
        else:
            body.append("\n  Say something to get started…", style="dim italic")

        chat = Panel(
            body,
            title="[bold dim]Conversation[/bold dim]",
            box=box.ROUNDED,
            border_style="dim white",
            padding=(1, 2),
        )

        icon, colour, label = _STATES.get(state, ("●", "white", state.upper()))

        # ── Avatar ───────────────────────────────────────────────────────────
        av_text = Text(justify="center")
        for ln in _av_frame(state, _tick):
            av_text.append(ln + "\n", style=colour)
        avatar = Panel(
            Align.center(av_text, vertical="middle"),
            box=box.ROUNDED,
            border_style=colour,
            padding=(1, 0),
        )

        if state in ("listening", "recording"):
            filled    = min(int(level / threshold * (_BAR_WIDTH // 2)), _BAR_WIDTH)
            bar_chars = "█" * filled + "░" * (_BAR_WIDTH - filled)
            bar_col   = "bright_red" if state == "recording" else "bright_green"
            status_body = Text.assemble(
                (f"  {icon}  [", "white"),
                (bar_chars,      f"bold {bar_col}"),
                ("]  ",          "white"),
                (f"{spinner}  ", "dim"),
                (label,          f"bold {colour}"),
            )
        else:
            status_body = Text.assemble(
                (f"  {icon}  ",  ""),
                (f"{spinner}  ", "dim"),
                (label,          f"bold {colour}"),
            )

        status = Panel(
            status_body,
            box=box.ROUNDED,
            border_style=colour,
            padding=(0, 1),
        )

        layout = Layout()
        layout.split_column(
            Layout(header, name="header", size=6),
            Layout(name="body"),
            Layout(status, name="status", size=4),
        )
        layout["body"].split_row(
            Layout(avatar, name="avatar", size=15),
            Layout(chat,   name="chat"),
        )
        return layout


_ui_renderable = _NadoUI()


def is_active() -> bool:
    return _active


def set_state(state: str) -> None:
    global _state
    if not _active:
        return
    with _lock:
        _state = state


def update_level(rms: float, threshold: float, recording: bool = False) -> None:
    global _level, _threshold, _state
    if not _active:
        return
    with _lock:
        _level     = rms
        _threshold = threshold
        _state     = "recording" if recording else "listening"


def add_user(text: str) -> None:
    if not _active:
        return
    with _lock:
        _messages.append(("user", text))


def add_nado(text: str) -> None:
    if not _active:
        return
    with _lock:
        _messages.append(("nado", text))


def start() -> None:
    global _active, _live, _original_log_handlers

    root = logging.getLogger()
    _original_log_handlers = root.handlers[:]
    for h in root.handlers[:]:
        root.removeHandler(h)
    fh = logging.FileHandler("/tmp/nado.log", mode="a")
    fh.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s -- %(message)s",
        datefmt="%H:%M:%S",
    ))
    root.addHandler(fh)

    _live = Live(
        _ui_renderable,
        screen=True,
        auto_refresh=True,
        refresh_per_second=12,
        console=Console(),
    )
    _live.start()
    _active = True


def stop() -> None:
    global _active, _live

    _active = False

    if _live:
        try:
            _live.stop()
        except Exception:
            pass
        _live = None

    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
    for h in _original_log_handlers:
        root.addHandler(h)
