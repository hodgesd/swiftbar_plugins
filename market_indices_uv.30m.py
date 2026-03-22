#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "yfinance>=0.2.0",
# ]
# ///

# <swiftbar.title>Market Indices Quick Glance</swiftbar.title>
# <swiftbar.version>v1.0</swiftbar.version>
# <swiftbar.author>Derrick Hodges</swiftbar.author>
# <swiftbar.author.github>hodgesd</swiftbar.author.github>
# <swiftbar.desc>Major market indices via ETF proxies with EMA-based signals</swiftbar.desc>
# <swiftbar.dependencies>uv, yfinance</swiftbar.dependencies>

import json
import math
from pathlib import Path
from typing import Optional

SYMBOLS = ["VOO", "QQQ", "DIA", "VXUS"]
LABELS = {
    "VOO": "S&P 500",
    "QQQ": "NASDAQ",
    "DIA": "Dow Jones",
    "VXUS": "Int'l",
}

STATE_FILE = Path.home() / ".swiftbar_market_cycle"
CACHE_FILE = Path.home() / ".swiftbar_market_cache"

# 1 year ensures enough trading days for a proper 200-day EMA seed
HISTORY_PERIOD = "1y"


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
) -> tuple[str, str]:
    """Return (label, color) in priority order: STRONG BUY > BUY > WATCH > NEUTRAL."""
    if ema200 is not None and price < ema200:
        return "STRONG BUY", "green"
    if ema50 is not None and price < ema50:
        return "BUY", "blue"
    if daily_pct < -1.0:
        return "WATCH", "orange"
    return "NEUTRAL", ""


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

            results[symbol] = {
                "price": price,
                "daily_pct": daily_pct,
                "ema50": ema50,
                "ema200": ema200,
            }
        except Exception:
            continue
    return results


def render(results: dict) -> None:
    if not results:
        print("📊 N/A")
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
        print(f"📊 {hero} {sign}{pct:.1f}% | color={color}")
    else:
        print(f"📊 {hero} N/A")

    print("---")

    for sym in SYMBOLS:
        if sym not in results:
            print(f"{sym} ({LABELS[sym]}) — unavailable")
            continue

        d = results[sym]
        price = d["price"]
        pct = d["daily_pct"]
        ema50 = d["ema50"]
        ema200 = d["ema200"]

        sign = "+" if pct >= 0 else ""
        row_color = "green" if pct >= 0 else "red"
        print(
            f"{sym} ({LABELS[sym]})  ${price:.2f}  {sign}{pct:.1f}% | color={row_color}"
        )

        if ema50 is not None:
            dist50 = ((price - ema50) / ema50) * 100
            print(f"--50 EMA: ${ema50:.2f}  ({dist50:+.1f}%)")
        else:
            print("--50 EMA: insufficient data")

        if ema200 is not None:
            dist200 = ((price - ema200) / ema200) * 100
            print(f"--200 EMA: ${ema200:.2f}  ({dist200:+.1f}%)")
        else:
            print("--200 EMA: insufficient data")

        signal, sig_color = evaluate_signal(price, ema50, ema200, pct)
        color_attr = f" color={sig_color}" if sig_color else ""
        print(f"--Signal: {signal} |{color_attr}")

    print("---")
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
            print("📊 ⚠️")
            print("---")
            print(f"Error: {exc}")
            print("---")
            print("Refresh | refresh=true")


if __name__ == "__main__":
    main()
