#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "yfinance>=0.2.0",
# ]
# ///

# <swiftbar.title>Market Indices Quick Glance</swiftbar.title>
# <swiftbar.version>v2.0</swiftbar.version>
# <swiftbar.author>Derrick Hodges</swiftbar.author>
# <swiftbar.author.github>hodgesd</swiftbar.author.github>
# <swiftbar.desc>Major market indices via ETF proxies with EMA-based signals</swiftbar.desc>
# <swiftbar.dependencies>uv, yfinance</swiftbar.dependencies>

import datetime
import json
import math
from pathlib import Path
from typing import Optional

SYMBOLS = ["VOO", "QQQ", "IWM", "VXUS", "GLD"]
LABELS = {
    "VOO": "S&P 500",
    "QQQ": "NASDAQ",
    "IWM": "Russell 2000",
    "VXUS": "Int'l Markets",
    "GLD": "Gold",
}
YAHOO_URL = "https://finance.yahoo.com/quote"

STATE_FILE = Path.home() / ".swiftbar_market_cycle"
CACHE_FILE = Path.home() / ".swiftbar_market_cache"

HISTORY_PERIOD = "1y"

SIGNAL_PRIORITY = {
    "STRONG BUY": 0,
    "BUY": 1,
    "ACCUMULATE": 2,
    "WATCH": 3,
    "NEUTRAL": 4,
}
SIGNAL_COLORS = {
    "STRONG BUY": "green",
    "BUY": "dodgerblue",
    "ACCUMULATE": "steelblue",
    "WATCH": "orange",
    "NEUTRAL": "",
}


def get_next_symbol() -> str:
    """Read the cycle index, return the current symbol, and advance for next run."""
    try:
        idx = int(STATE_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        idx = 0
    symbol = SYMBOLS[idx % len(SYMBOLS)]
    STATE_FILE.write_text(str((idx + 1) % len(SYMBOLS)))
    return symbol


def calculate_ema(prices: list[float], period: int) -> Optional[float]:
    """Compute EMA with an SMA seed over the initial window."""
    if len(prices) < period:
        return None
    sma = sum(prices[:period]) / period
    k = 2 / (period + 1)
    ema = sma
    for price in prices[period:]:
        ema = (price - ema) * k + ema
    return ema


def evaluate_signal(
    price: float,
    ema50: Optional[float],
    ema200: Optional[float],
    daily_pct: float,
) -> str:
    """Tiered mean-reversion signals with distance thresholds."""
    if ema200 is not None:
        dist200 = ((price - ema200) / ema200) * 100
        if dist200 < -5.0:
            return "STRONG BUY"
        if dist200 < 0:
            return "BUY"
    if ema50 is not None and price < ema50:
        return "ACCUMULATE"
    if daily_pct < -1.0:
        return "WATCH"
    return "NEUTRAL"


def detect_trend(ema50: Optional[float], ema200: Optional[float]) -> str:
    if ema50 is None or ema200 is None:
        return "N/A"
    return "Bullish" if ema50 > ema200 else "Bearish"


def fetch_data() -> dict:
    import yfinance as yf

    results: dict = {}
    for symbol in SYMBOLS:
        try:
            hist = yf.Ticker(symbol).history(period=HISTORY_PERIOD, interval="1d")
            if hist.empty or len(hist) < 2:
                continue

            closes = [
                c
                for c in hist["Close"].tolist()
                if not (isinstance(c, float) and math.isnan(c))
            ]
            if len(closes) < 2:
                continue

            price = closes[-1]
            prev = closes[-2]
            daily_pct = ((price - prev) / prev) * 100

            ema50 = calculate_ema(closes, 50)
            ema200 = calculate_ema(closes, 200)

            signal = evaluate_signal(price, ema50, ema200, daily_pct)
            trend = detect_trend(ema50, ema200)

            dist50 = ((price - ema50) / ema50) * 100 if ema50 else None
            dist200 = ((price - ema200) / ema200) * 100 if ema200 else None

            results[symbol] = {
                "price": price,
                "daily_pct": daily_pct,
                "ema50": ema50,
                "ema200": ema200,
                "dist50": dist50,
                "dist200": dist200,
                "signal": signal,
                "trend": trend,
            }
        except Exception:
            continue
    return results


def build_tooltip(sym: str, d: dict) -> str:
    parts = [LABELS[sym]]
    if d["ema50"] is not None:
        parts.append(f"50 EMA: ${d['ema50']:.2f} ({d['dist50']:+.1f}%)")
    if d["ema200"] is not None:
        parts.append(f"200 EMA: ${d['ema200']:.2f} ({d['dist200']:+.1f}%)")
    parts.append(f"Trend: {d['trend']}")
    return " | ".join(parts)


def render(results: dict) -> None:
    if not results:
        print("N/A | sfimage=chart.line.uptrend.xyaxis")
        print("---")
        print("Market data unavailable")
        print("---")
        print("Refresh | refresh=true")
        return

    hero = get_next_symbol()
    if hero in results:
        d = results[hero]
        pct = d["daily_pct"]
        sign = "+" if pct >= 0 else ""
        color = "green" if pct >= 0 else "red"
        print(f"{hero} {sign}{pct:.1f}% | sfimage=chart.line.uptrend.xyaxis color={color}")
    else:
        print(f"{hero} N/A | sfimage=chart.line.uptrend.xyaxis")

    print("---")

    ranked = sorted(
        ((sym, results[sym]) for sym in SYMBOLS if sym in results),
        key=lambda pair: SIGNAL_PRIORITY.get(pair[1]["signal"], 99),
    )

    for sym, d in ranked:
        price = d["price"]
        pct = d["daily_pct"]
        signal = d["signal"]

        sign = "+" if pct >= 0 else ""
        sig_color = SIGNAL_COLORS.get(signal, "")
        color_attr = f" color={sig_color}" if sig_color else ""
        tooltip = build_tooltip(sym, d)

        print(
            f"{signal}  {sym}  ${price:.2f}  {sign}{pct:.1f}%"
            f' | href={YAHOO_URL}/{sym} tooltip="{tooltip}"{color_attr}'
        )

    for sym in SYMBOLS:
        if sym not in results:
            print(f"—  {sym}  unavailable | href={YAHOO_URL}/{sym}")

    print("---")
    print(f"Updated {datetime.datetime.now().strftime('%I:%M %p')}")
    print("Refresh | refresh=true")


def main() -> None:
    try:
        results = fetch_data()
        try:
            CACHE_FILE.write_text(json.dumps(results))
        except Exception:
            pass
        render(results)
    except Exception as exc:
        try:
            cached = json.loads(CACHE_FILE.read_text())
            render(cached)
        except Exception:
            print("⚠️ | sfimage=chart.line.uptrend.xyaxis")
            print("---")
            print(f"Error: {exc}")
            print("---")
            print("Refresh | refresh=true")


if __name__ == "__main__":
    main()
