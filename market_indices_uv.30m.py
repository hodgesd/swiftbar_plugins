#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "yfinance>=0.2.0",
# ]
# ///

# <swiftbar.title>Market Indices Quick Glance</swiftbar.title>
# <swiftbar.version>v3.0</swiftbar.version>
# <swiftbar.author>Derrick Hodges</swiftbar.author>
# <swiftbar.author.github>hodgesd</swiftbar.author.github>
# <swiftbar.desc>Major market indices via ETF proxies with EMA-based signals</swiftbar.desc>
# <swiftbar.dependencies>uv, yfinance</swiftbar.dependencies>

import datetime
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

SYMBOLS = ["VOO", "QQQ", "IWM", "VXUS", "GLD"]
LABELS = {
    "VOO": "S&P 500",
    "QQQ": "NASDAQ",
    "IWM": "Russell 2000",
    "VXUS": "Int'l Markets",
    "GLD": "Gold",
}
YAHOO_URL = "https://finance.yahoo.com/quote"

_plugin_data = os.getenv("SWIFTBAR_PLUGIN_DATA_PATH", "")
DATA_DIR = Path(_plugin_data).parent if _plugin_data else Path.home()
STATE_FILE = DATA_DIR / "market_cycle.json"
CACHE_FILE = DATA_DIR / "market_cache.json"

HISTORY_PERIOD = "1y"
CACHE_MAX_AGE_HOURS = 24

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


@dataclass
class MarketData:
    price: float
    daily_pct: float
    ema50: float | None
    ema200: float | None
    dist50: float | None
    dist200: float | None
    signal: str
    trend: str


def pct_distance(price: float, ema: float) -> float:
    return ((price - ema) / ema) * 100


def format_change(pct: float) -> tuple[str, str]:
    sign = "+" if pct >= 0 else ""
    color = "green" if pct >= 0 else "red"
    return f"{sign}{pct:.1f}%", color


def get_next_symbol() -> str:
    """Read the cycle index, return the current symbol, and advance for next run."""
    try:
        idx = int(STATE_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        idx = 0
    symbol = SYMBOLS[idx % len(SYMBOLS)]
    STATE_FILE.write_text(str((idx + 1) % len(SYMBOLS)))
    return symbol


def evaluate_signal(
    price: float,
    ema50: float | None,
    ema200: float | None,
    daily_pct: float,
) -> str:
    """Tiered mean-reversion signals with distance thresholds."""
    if ema200 is not None:
        dist200 = pct_distance(price, ema200)
        if dist200 < -5.0:
            return "STRONG BUY"
        if dist200 < 0:
            return "BUY"
    if ema50 is not None and price < ema50:
        return "ACCUMULATE"
    if daily_pct < -1.0:
        return "WATCH"
    return "NEUTRAL"


def detect_trend(ema50: float | None, ema200: float | None) -> str:
    if ema50 is None or ema200 is None:
        return "N/A"
    return "Bullish" if ema50 > ema200 else "Bearish"


def calculate_ema(prices: list[float], period: int) -> float | None:
    """Compute EMA with an SMA seed over the initial window."""
    if len(prices) < period:
        return None
    sma = sum(prices[:period]) / period
    k = 2 / (period + 1)
    ema = sma
    for price in prices[period:]:
        ema = (price - ema) * k + ema
    return ema


def fetch_data() -> dict[str, MarketData]:
    import yfinance as yf

    hist = yf.download(
        SYMBOLS, period=HISTORY_PERIOD, interval="1d",
        group_by="ticker", progress=False,
    )

    results: dict[str, MarketData] = {}
    for symbol in SYMBOLS:
        try:
            closes = hist[symbol]["Close"].dropna().tolist()
            if len(closes) < 2:
                continue

            price = closes[-1]
            prev = closes[-2]
            daily_pct = ((price - prev) / prev) * 100

            ema50 = calculate_ema(closes, 50)
            ema200 = calculate_ema(closes, 200)

            signal = evaluate_signal(price, ema50, ema200, daily_pct)
            trend = detect_trend(ema50, ema200)

            results[symbol] = MarketData(
                price=price,
                daily_pct=daily_pct,
                ema50=ema50,
                ema200=ema200,
                dist50=pct_distance(price, ema50) if ema50 else None,
                dist200=pct_distance(price, ema200) if ema200 else None,
                signal=signal,
                trend=trend,
            )
        except (KeyError, TypeError, IndexError):
            continue
    return results


def build_tooltip(sym: str, d: MarketData) -> str:
    parts = [LABELS[sym]]
    if d.ema50 is not None:
        parts.append(f"50 EMA: ${d.ema50:.2f} ({d.dist50:+.1f}%)")
    if d.ema200 is not None:
        parts.append(f"200 EMA: ${d.ema200:.2f} ({d.dist200:+.1f}%)")
    parts.append(f"Trend: {d.trend}")
    return " | ".join(parts)


def render(results: dict[str, MarketData]) -> None:
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
        change_str, color = format_change(d.daily_pct)
        print(f"{hero} {change_str} | sfimage=chart.line.uptrend.xyaxis color={color}")
    else:
        print(f"{hero} N/A | sfimage=chart.line.uptrend.xyaxis")

    print("---")

    ranked = sorted(
        ((sym, results[sym]) for sym in SYMBOLS if sym in results),
        key=lambda pair: SIGNAL_PRIORITY.get(pair[1].signal, 99),
    )

    for sym, d in ranked:
        change_str, _ = format_change(d.daily_pct)
        sig_color = SIGNAL_COLORS.get(d.signal, "")
        color_attr = f" color={sig_color}" if sig_color else ""
        tooltip = build_tooltip(sym, d)

        print(
            f"{d.signal}  {sym}  ${d.price:.2f}  {change_str}"
            f' | href={YAHOO_URL}/{sym} tooltip="{tooltip}"{color_attr}'
        )

    for sym in SYMBOLS:
        if sym not in results:
            print(f"—  {sym}  unavailable | href={YAHOO_URL}/{sym}")

    print("---")
    print(f"Updated {datetime.datetime.now().strftime('%I:%M %p')}")
    print("Refresh | refresh=true")


def save_cache(results: dict[str, MarketData]) -> None:
    try:
        payload = {
            "timestamp": datetime.datetime.now().isoformat(),
            "data": {sym: asdict(d) for sym, d in results.items()},
        }
        CACHE_FILE.write_text(json.dumps(payload))
    except Exception:
        pass


def load_cache() -> dict[str, MarketData] | None:
    try:
        payload = json.loads(CACHE_FILE.read_text())
        ts = datetime.datetime.fromisoformat(payload["timestamp"])
        age = datetime.datetime.now() - ts
        if age.total_seconds() > CACHE_MAX_AGE_HOURS * 3600:
            return None
        return {sym: MarketData(**d) for sym, d in payload["data"].items()}
    except Exception:
        return None


def main() -> None:
    try:
        results = fetch_data()
        save_cache(results)
        render(results)
    except Exception as exc:
        cached = load_cache()
        if cached:
            render(cached)
        else:
            print("⚠️ | sfimage=chart.line.uptrend.xyaxis")
            print("---")
            print(f"Error: {exc}")
            print("---")
            print("Refresh | refresh=true")


if __name__ == "__main__":
    main()
