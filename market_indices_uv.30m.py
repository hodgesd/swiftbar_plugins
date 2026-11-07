#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "yfinance>=0.2.0",
# ]
# ///

# <swiftbar.title>Market Indices Quick Glance</swiftbar.title>
# <swiftbar.version>v3.1</swiftbar.version>
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
ICON_UP = "chart.line.uptrend.xyaxis"
ICON_DOWN = "chart.line.downtrend.xyaxis"

_XDG_CACHE = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
CACHE_DIR = Path(_XDG_CACHE) / "swiftbar-plugins"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = CACHE_DIR / "market_cycle.json"
CACHE_FILE = CACHE_DIR / "market_cache.json"

# The EMA is seeded with a 200-close SMA, so it needs ~250 more closes to converge.
# With "1y" (252 closes) 59% of the "200 EMA" was still the seed and GLD read 3% high.
HISTORY_PERIOD = "2y"
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
    as_of: str = (
        ""  # ISO date of the last close; the default keeps v3.0 caches loadable
    )


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
        SYMBOLS,
        period=HISTORY_PERIOD,
        interval="1d",
        group_by="ticker",
        progress=False,
    )

    results: dict[str, MarketData] = {}
    for symbol in SYMBOLS:
        try:
            series = hist[symbol]["Close"].dropna()
            closes = series.tolist()
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
                as_of=series.index[-1].date().isoformat(),
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


def footer(results: dict[str, MarketData], cached_at: datetime.datetime | None) -> str:
    """Close date when it isn't today, then either the update time or a stale warning."""
    today = datetime.date.today().isoformat()
    latest = max((d.as_of for d in results.values() if d.as_of), default="")
    parts = []
    if latest and latest != today:
        parts.append(datetime.date.fromisoformat(latest).strftime("%a %m/%d close"))
    if cached_at:
        parts.append(f"cached {cached_at:%I:%M %p %a}, fetch failed")
    else:
        parts.append(f"updated {datetime.datetime.now():%I:%M %p}")
    line = " · ".join(parts)
    line = line[0].upper() + line[1:]
    return line + (" | color=orange" if cached_at else "")


def render(results: dict[str, MarketData], cached_at: datetime.datetime | None) -> None:
    hero = get_next_symbol()
    if hero in results:
        d = results[hero]
        change_str, color = format_change(d.daily_pct)
        icon = ICON_DOWN if d.daily_pct < 0 else ICON_UP
        print(f"{hero} {change_str} | sfimage={icon} color={color}")
    else:
        print(f"{hero} N/A | sfimage={ICON_UP}")

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
    print(footer(results, cached_at))
    print("Refresh | refresh=true")


def render_error(message: str) -> None:
    print(f"⚠️ | sfimage={ICON_UP}")
    print("---")
    print(f"Error: {message}")
    print("---")
    print("Refresh | refresh=true")


def save_cache(results: dict[str, MarketData]) -> None:
    try:
        payload = {
            "timestamp": datetime.datetime.now().isoformat(),
            "data": {sym: asdict(d) for sym, d in results.items()},
        }
        CACHE_FILE.write_text(json.dumps(payload))
    except Exception:  # noqa: BLE001, S110 — a cache write failure must not break the menu
        pass


def load_cache() -> tuple[dict[str, MarketData], datetime.datetime | None]:
    """Last good data and when it was fetched; empty when missing, unreadable or over 24h old."""
    try:
        payload = json.loads(CACHE_FILE.read_text())
        ts = datetime.datetime.fromisoformat(payload["timestamp"])
        age = datetime.datetime.now() - ts
        if age.total_seconds() > CACHE_MAX_AGE_HOURS * 3600:
            return {}, None
        return {sym: MarketData(**d) for sym, d in payload["data"].items()}, ts
    except Exception:  # noqa: BLE001 — a bad cache is the same as no cache
        return {}, None


def main() -> None:
    error = ""
    try:
        fresh = fetch_data()
    except Exception as exc:  # noqa: BLE001 — any failure degrades to cache
        fresh, error = {}, str(exc)
    cached, cached_at = load_cache()
    # yfinance returns an empty table instead of raising when Yahoo is unreachable, so an
    # empty result must never replace the last good cache (it did, until v3.1).
    if fresh:
        save_cache(fresh)
    results = {**cached, **fresh}
    if not results:
        render_error(error or "no data from Yahoo")
        return
    render(results, cached_at if len(fresh) < len(results) else None)


if __name__ == "__main__":
    main()
