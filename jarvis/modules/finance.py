"""
modules/finance.py — Gold price lookup and price-target alerting.

Uses gold-api.com (free, no API key) for the live XAU/USD spot price.
Price targets are stored via memory.set_preference so they survive restarts;
the actual "alert once when target is reached" polling lives in
bot/telegram_bot.py's job queue (see _check_gold_target), matching the
pattern already used for reminder delivery.
"""

import datetime
import logging
import urllib.error
import urllib.request
import json
from typing import Optional

import memory
from store.db import get_connection

logger = logging.getLogger(__name__)

_SNAPSHOT_LOOKBACK_HOURS = 24

_GOLD_API_URL = "https://api.gold-api.com/price/XAU"
_REQUEST_TIMEOUT_SECONDS = 10

_TER_API_URL = "https://api.ter.bt/prices"
_TER_SYMBOLS = {"USD": "TERUSD", "INR": "TERINR", "BTN": "TERBTN"}
# api.ter.bt returns prices scaled by 10,000 (e.g. 13456 -> 1.3456) — confirmed
# against the real-world TER price (~BTN 128, ~USD 1.4); not documented by the API.
_TER_PRICE_SCALE = 10_000


def _fetch_gold_price() -> Optional[dict]:
    """Fetch the current gold spot price from gold-api.com.

    Returns:
        A dict with keys 'price' (float, USD per troy ounce) and
        'updatedAt', or None if the request failed.
    """
    try:
        with urllib.request.urlopen(_GOLD_API_URL, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
            return json.load(resp)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        logger.error("Gold price fetch failed: %s", exc)
        return None


def gold_price(chat_id: str = "", args: Optional[list[str]] = None) -> str:
    """Return the current gold price, or set/clear a price-target alert.

    Args:
        chat_id: The Telegram chat ID (used only for the 'target' subcommand).
        args: Optional subcommand — ["target", "<price>"] to set an alert
              threshold, or ["target", "clear"] to remove it. No args
              (or anything else) just reports the current price.

    Returns:
        A plain-text reply.
    """
    args = args or []

    if args and args[0].lower() == "target":
        return _handle_target(args[1:])

    data = _fetch_gold_price()
    if data is None:
        return "Couldn't reach the gold price service — try again shortly."

    price = data["price"]
    target = memory.get_preference("gold_price_target")
    line = f"Gold (XAU/USD): ${price:,.2f} per troy ounce."
    if target:
        line += f" Your alert target is ${float(target):,.2f}."
    return line


def _handle_target(args: list[str]) -> str:
    """Set or clear the stored gold price-target alert.

    Args:
        args: ["<price>"] to set a target, or ["clear"] to remove it.

    Returns:
        A confirmation or usage string.
    """
    if not args:
        return "Usage: /gold target <price> | /gold target clear"

    if args[0].lower() == "clear":
        memory.set_preference("gold_price_target", None)
        memory.set_preference("gold_price_target_alerted", False)
        return "Gold price target alert cleared."

    try:
        target_price = float(args[0])
    except ValueError:
        return "Usage: /gold target <price> | /gold target clear"

    if target_price <= 0:
        return "Target price must be positive."

    memory.set_preference("gold_price_target", target_price)
    memory.set_preference("gold_price_target_alerted", False)
    return f"Gold price target set to ${target_price:,.2f}. I'll alert you once it's reached."


def _fetch_ter_prices() -> Optional[dict]:
    """Fetch all TER product prices from api.ter.bt.

    Returns:
        A dict keyed by product_symbol (e.g. "TERUSD"), each value the raw
        price entry dict, or None if the request failed.
    """
    try:
        with urllib.request.urlopen(_TER_API_URL, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
            data = json.load(resp)
        return {entry["product_symbol"]: entry for entry in data.get("prices", [])}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
        logger.error("TER price fetch failed: %s", exc)
        return None


def ter_price(chat_id: str = "", args: Optional[list[str]] = None) -> str:
    """Return TER token buy/sell prices.

    Args:
        chat_id: Unused — kept for a consistent command-handler signature.
        args: Optional currency code (USD, INR, or BTN); defaults to USD.
              Pass "all" to list every currency.

    Returns:
        A plain-text reply with ask (buy) and bid (sell) prices.
    """
    args = args or []
    currency = args[0].upper() if args else "USD"

    prices = _fetch_ter_prices()
    if prices is None:
        return "Couldn't reach the TER price service — try again shortly."

    if currency == "ALL":
        lines = [_format_ter_line(sym, cur, prices) for cur, sym in _TER_SYMBOLS.items()]
        return "\n".join(line for line in lines if line)

    symbol = _TER_SYMBOLS.get(currency)
    if symbol is None:
        return f"Unknown currency '{currency}'. Use USD, INR, BTN, or 'all'."

    line = _format_ter_line(symbol, currency, prices)
    return line or f"No price data available for TER/{currency}."


def ter_target(chat_id: str = "", args: Optional[list[str]] = None) -> str:
    """Set or clear TER buy/sell price-target alerts (denominated in BTN).

    Args:
        chat_id: Unused — kept for a consistent command-handler signature.
        args: ["buy", "<price>"] to alert when the BTN buy (ask) price drops
              to or below it; ["sell", "<price>"] to alert when the BTN sell
              (bid) price rises to or above it; ["clear"] to remove both.

    Returns:
        A confirmation or usage string.
    """
    args = args or []
    if not args:
        return "Usage: /ter target buy <price> | /ter target sell <price> | /ter target clear"

    if args[0].lower() == "clear":
        memory.set_preference("ter_buy_alert_below", None)
        memory.set_preference("ter_buy_alerted", False)
        memory.set_preference("ter_sell_alert_above", None)
        memory.set_preference("ter_sell_alerted", False)
        return "TER price target alerts cleared."

    if len(args) < 2 or args[0].lower() not in ("buy", "sell"):
        return "Usage: /ter target buy <price> | /ter target sell <price> | /ter target clear"

    try:
        target_price = float(args[1])
    except ValueError:
        return "Usage: /ter target buy <price> | /ter target sell <price> | /ter target clear"

    if target_price <= 0:
        return "Target price must be positive."

    side = args[0].lower()
    if side == "buy":
        memory.set_preference("ter_buy_alert_below", target_price)
        memory.set_preference("ter_buy_alerted", False)
        return f"I'll alert you when TER/BTN buy price drops to {target_price:,.2f} or below."

    memory.set_preference("ter_sell_alert_above", target_price)
    memory.set_preference("ter_sell_alerted", False)
    return f"I'll alert you when TER/BTN sell price rises to {target_price:,.2f} or above."


def check_ter_targets() -> list[str]:
    """Check the stored TER buy/sell BTN price targets, one-shot per target.

    Mirrors check_price_target()'s dedup pattern but independently for each
    side, since a buy-drop and a sell-rise alert can both be armed at once.

    Returns:
        A list of alert messages for any target(s) just reached (usually 0 or 1).
    """
    buy_target = memory.get_preference("ter_buy_alert_below")
    sell_target = memory.get_preference("ter_sell_alert_above")
    if not buy_target and not sell_target:
        return []

    prices = _fetch_ter_prices()
    if prices is None:
        return []

    btn = prices.get("TERBTN")
    if btn is None:
        return []

    ask = btn["ask_price"] / _TER_PRICE_SCALE
    bid = btn["bid_price"] / _TER_PRICE_SCALE
    alerts: list[str] = []

    if buy_target and not memory.get_preference("ter_buy_alerted") and ask <= float(buy_target):
        memory.set_preference("ter_buy_alerted", True)
        alerts.append(f"TER/BTN buy price dropped to {ask:,.2f} (target was {float(buy_target):,.2f}).")

    if sell_target and not memory.get_preference("ter_sell_alerted") and bid >= float(sell_target):
        memory.set_preference("ter_sell_alerted", True)
        alerts.append(f"TER/BTN sell price rose to {bid:,.2f} (target was {float(sell_target):,.2f}).")

    return alerts


def _format_ter_line(symbol: str, currency: str, prices: dict) -> Optional[str]:
    """Format one TER product's ask/bid line, or None if missing from `prices`."""
    entry = prices.get(symbol)
    if entry is None:
        return None
    ask = entry["ask_price"] / _TER_PRICE_SCALE
    bid = entry["bid_price"] / _TER_PRICE_SCALE
    decimals = 4 if currency == "USD" else 2
    return f"TER/{currency} — buy: {ask:,.{decimals}f}, sell: {bid:,.{decimals}f}"


def record_price_snapshots() -> None:
    """Fetch and store current gold/TER prices for later change comparison.

    Intended to be polled periodically (e.g. hourly) so digest.py has
    historical data to diff against. Silently skips any price that fails to
    fetch rather than failing the whole snapshot.
    """
    now = datetime.datetime.now().isoformat()
    rows: list[tuple[str, float]] = []

    gold = _fetch_gold_price()
    if gold is not None:
        rows.append(("GOLD_USD", gold["price"]))

    ter = _fetch_ter_prices()
    if ter is not None and "TERBTN" in ter:
        rows.append(("TER_BTN", ter["TERBTN"]["ask_price"] / _TER_PRICE_SCALE))

    if not rows:
        return

    with get_connection() as conn:
        conn.executemany(
            "INSERT INTO price_snapshots (symbol, price, recorded_at) VALUES (?, ?, ?)",
            [(symbol, price, now) for symbol, price in rows],
        )


def _baseline_price(symbol: str) -> Optional[float]:
    """Return the most recent snapshot at or before _SNAPSHOT_LOOKBACK_HOURS ago."""
    cutoff = (datetime.datetime.now() - datetime.timedelta(hours=_SNAPSHOT_LOOKBACK_HOURS)).isoformat()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT price FROM price_snapshots WHERE symbol = ? AND recorded_at <= ? "
            "ORDER BY recorded_at DESC LIMIT 1",
            (symbol, cutoff),
        ).fetchone()
    return row["price"] if row else None


def price_change_summary() -> str:
    """Return a one-line-per-symbol summary of gold/TER price movement over ~24h.

    Returns:
        Plain text, e.g. "Gold: $4,150.90 (+0.3% vs 24h ago)". Symbols with no
        baseline snapshot yet (first run) report the current price only.
    """
    lines = []

    gold = _fetch_gold_price()
    if gold is not None:
        lines.append(_change_line("Gold", gold["price"], _baseline_price("GOLD_USD"), "$"))

    ter = _fetch_ter_prices()
    if ter is not None and "TERBTN" in ter:
        current = ter["TERBTN"]["ask_price"] / _TER_PRICE_SCALE
        lines.append(_change_line("TER/BTN", current, _baseline_price("TER_BTN"), ""))

    return "\n".join(lines) if lines else "Price data unavailable."


def _change_line(label: str, current: float, baseline: Optional[float], currency_prefix: str) -> str:
    """Format one price-change line, or just the current price if no baseline exists."""
    if baseline is None or baseline == 0:
        return f"{label}: {currency_prefix}{current:,.2f} (no 24h baseline yet)"

    pct_change = (current - baseline) / baseline * 100
    arrow = "▲" if pct_change > 0 else ("▼" if pct_change < 0 else "→")
    return f"{label}: {currency_prefix}{current:,.2f} ({arrow} {pct_change:+.1f}% vs 24h ago)"


def check_price_target() -> Optional[str]:
    """Check whether the stored gold price target has been reached.

    Fires at most once per target (tracked via the 'gold_price_target_alerted'
    preference) so it doesn't spam on every poll once the target is hit.

    Returns:
        An alert message if the target was just reached, else None.
    """
    target = memory.get_preference("gold_price_target")
    if not target:
        return None
    if memory.get_preference("gold_price_target_alerted"):
        return None

    data = _fetch_gold_price()
    if data is None:
        return None

    price = data["price"]
    if price >= float(target):
        memory.set_preference("gold_price_target_alerted", True)
        return f"Gold hit your target: ${price:,.2f} (target was ${float(target):,.2f})."
    return None
