"""tests/test_finance.py — price-target math, without hitting real HTTP APIs.

Every test monkeypatches finance._fetch_gold_price / _fetch_ter_prices so
these run fast, offline, and deterministically — the point is to test the
target/threshold/scale-factor arithmetic, not the network calls.
"""

import memory
from modules import finance


def test_handle_target_set_and_clear():
    result = finance._handle_target(["2650"])
    assert "2,650.00" in result
    assert memory.get_preference("gold_price_target") == 2650.0

    finance._handle_target(["clear"])
    assert memory.get_preference("gold_price_target") is None


def test_handle_target_rejects_invalid_input():
    assert "Usage" in finance._handle_target([])
    assert "Usage" in finance._handle_target(["not-a-number"])
    assert "must be positive" in finance._handle_target(["-5"])


def test_check_price_target_fires_once_when_reached(monkeypatch):
    memory.set_preference("gold_price_target", 2000.0)
    monkeypatch.setattr(finance, "_fetch_gold_price", lambda: {"price": 2050.0})

    alert = finance.check_price_target()
    assert alert is not None
    assert "2,050.00" in alert

    # Second poll at the same (or higher) price should not re-fire
    assert finance.check_price_target() is None


def test_check_price_target_silent_below_target(monkeypatch):
    memory.set_preference("gold_price_target", 3000.0)
    monkeypatch.setattr(finance, "_fetch_gold_price", lambda: {"price": 2050.0})

    assert finance.check_price_target() is None


def test_check_price_target_noop_without_a_target():
    assert finance.check_price_target() is None


def _fake_ter_prices(ask_scaled: int, bid_scaled: int) -> dict:
    return {"TERBTN": {"ask_price": ask_scaled, "bid_price": bid_scaled}}


def test_check_ter_targets_buy_side(monkeypatch):
    finance.ter_target("owner", ["buy", "1.30"])
    # ask_price is scaled by 10,000 per api.ter.bt's undocumented encoding
    monkeypatch.setattr(finance, "_fetch_ter_prices", lambda: _fake_ter_prices(12800, 12500))

    alerts = finance.check_ter_targets()
    assert len(alerts) == 1
    assert "buy price dropped" in alerts[0]
    assert "1.28" in alerts[0]

    # Already alerted — should not fire again even if still below target
    assert finance.check_ter_targets() == []


def test_check_ter_targets_sell_side(monkeypatch):
    finance.ter_target("owner", ["sell", "1.30"])
    monkeypatch.setattr(finance, "_fetch_ter_prices", lambda: _fake_ter_prices(13500, 13200))

    alerts = finance.check_ter_targets()
    assert len(alerts) == 1
    assert "sell price rose" in alerts[0]
    assert "1.32" in alerts[0]


def test_check_ter_targets_clear_removes_both():
    finance.ter_target("owner", ["buy", "1.30"])
    finance.ter_target("owner", ["sell", "1.40"])
    finance.ter_target("owner", ["clear"])

    assert memory.get_preference("ter_buy_alert_below") is None
    assert memory.get_preference("ter_sell_alert_above") is None


def test_change_line_reports_no_baseline():
    line = finance._change_line("Gold", 2650.0, None, "$")
    assert "no 24h baseline yet" in line


def test_change_line_reports_percentage_up_and_down():
    up = finance._change_line("Gold", 2650.0, 2600.0, "$")
    assert "▲" in up
    assert "+1.9%" in up

    down = finance._change_line("Gold", 2500.0, 2600.0, "$")
    assert "▼" in down
    assert "-3.8%" in down
