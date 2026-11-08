"""Cache regression tests for market_indices_uv.30m.py.

yfinance returns an empty table instead of raising when Yahoo is unreachable; v3.0 then
overwrote the last good cache with nothing. Nothing here touches the network.

Run:  uv run --with pytest pytest tests/test_market_indices.py
"""

import importlib.util
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

os.environ["XDG_CACHE_HOME"] = (
    tempfile.mkdtemp()
)  # the module creates its cache dir on import

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "market_indices", ROOT / "market_indices_uv.30m.py"
)
mi = importlib.util.module_from_spec(spec)
sys.modules["market_indices"] = mi
spec.loader.exec_module(mi)

GOOD = mi.MarketData(
    price=100.0,
    daily_pct=0.5,
    ema50=99.0,
    ema200=98.0,
    dist50=1.0,
    dist200=2.0,
    signal="NEUTRAL",
    trend="Bullish",
    as_of="2026-11-06",
)


def test_empty_fetch_keeps_cache_and_marks_it_stale(monkeypatch, capsys):
    mi.save_cache({"VOO": GOOD})
    saved_at = mi.load_cache()[1]
    monkeypatch.setattr(mi, "fetch_data", dict)
    mi.main()
    out = capsys.readouterr().out
    assert mi.load_cache() == (
        {"VOO": GOOD},
        saved_at,
    )  # not re-saved, so not re-stamped
    assert "VOO  $100.00" in out
    assert "fetch failed | color=orange" in out


def test_partial_fetch_is_saved_merged_over_cache(monkeypatch, capsys):
    mi.save_cache({"VOO": GOOD, "QQQ": replace(GOOD, price=90.0)})
    fresh = {sym: GOOD for sym in mi.SYMBOLS if sym != "VOO"}
    monkeypatch.setattr(mi, "fetch_data", lambda: fresh)
    mi.main()
    assert "fetch failed | color=orange" in capsys.readouterr().out
    cached, _ = mi.load_cache()
    assert set(cached) == set(mi.SYMBOLS)
    assert cached["QQQ"].price == 100.0  # fresh wins over cached


def test_full_fetch_is_saved_and_not_marked_stale(monkeypatch, capsys):
    mi.save_cache({"VOO": GOOD})
    fresh = {sym: GOOD for sym in mi.SYMBOLS}
    monkeypatch.setattr(mi, "fetch_data", lambda: fresh)
    mi.main()
    assert "fetch failed" not in capsys.readouterr().out
    assert set(mi.load_cache()[0]) == set(mi.SYMBOLS)
