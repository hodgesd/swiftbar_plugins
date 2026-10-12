#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "feedparser>=6.0.0",
# ]
# ///

# <swiftbar.title>Deals: Micro Center + DeWalt</swiftbar.title>
# <swiftbar.version>v2.0</swiftbar.version>
# <swiftbar.author>Derrick Hodges</swiftbar.author>
# <swiftbar.author.github>hodgesd</swiftbar.author.github>
# <swiftbar.desc>Current Micro Center deals and DeWalt power-tool deals at Lowe's, via Slickdeals RSS. Click opens the store; ⌥-click opens the Slickdeals thread.</swiftbar.desc>
# <swiftbar.dependencies>uv, feedparser</swiftbar.dependencies>

# v2.0 (2026-10-11): microcenter.com now sits behind a Cloudflare JS challenge and
# Bing Shopping no longer exposes sale prices, so both sections come from
# Slickdeals search RSS instead. Kobalt replaced by DeWalt power tools.

from __future__ import annotations

import html
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import feedparser

SLICKDEALS_SEARCH = "https://slickdeals.net/newsearch.php?q={query}&rss=1"
MICROCENTER_QUERY = "microcenter"
DEWALT_QUERY = "dewalt+lowes"
MICROCENTER_STORE_URL = "https://www.microcenter.com/site/content/top-deals.aspx"
LOWES_DEWALT_URL = "https://www.lowes.com/pl/power-tools/dewalt/4294612237"

MAX_ITEMS = 12
MAX_AGE_DAYS = 14
MAX_TITLE = 72

# Rough "is this a power tool" signal; matches rank above bits, blades and accessories.
POWER_TOOL_WORDS = re.compile(
    r"\b(drill|driver|impact|hammer|saw|sander|grinder|router|planer|jigsaw|"
    r"nailer|stapler|trimmer|blower|mower|chainsaw|vacuum|vac|compressor|"
    r"combo kit|kit|battery|batteries|charger|flashlight|light|inflator|"
    r"multi-?tool|oscillating tool|pressure washer|shear|polisher|heat gun|"
    r"ratchet|wrench|cut-?off|tool only|brushless|20v|60v|flexvolt|xr\b)",
    re.I,
)
ACCESSORY_WORDS = re.compile(
    r"\b(bit set|bits?\b|blades?\b|holder|extension|case|bag|socket|screwdriver|"
    r"knife|tape measure|glove|pencil|marker|sandpaper|disc|abrasive|hose|"
    r"ear|safety|level|clamp|knee)",
    re.I,
)


@dataclass
class Deal:
    title: str
    thread_url: str
    store_url: str
    published: Optional[datetime]
    expired: bool
    price: Optional[float]
    source: str  # store name as Slickdeals reported it

    @property
    def age(self) -> str:
        if not self.published:
            return ""
        delta = datetime.now(timezone.utc) - self.published
        hours = int(delta.total_seconds() // 3600)
        if hours < 1:
            return f"{max(int(delta.total_seconds() // 60), 0)}m"
        if hours < 24:
            return f"{hours}h"
        return f"{hours // 24}d"

    @property
    def is_power_tool(self) -> bool:
        return bool(POWER_TOOL_WORDS.search(self.title)) and not (
            ACCESSORY_WORDS.search(self.title) and not re.search(r"combo|kit|tool only", self.title, re.I)
        )


def clean(text: str) -> str:
    return html.unescape(text).replace("|", "⎮").strip()


def trim(text: str, limit: int = MAX_TITLE) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def first_price(text: str) -> Optional[float]:
    m = re.search(r"\$\s?([\d,]+(?:\.\d{1,2})?)", text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def parse_entry(entry) -> Deal:
    title = clean(entry.get("title", "(no title)"))
    expired = bool(re.match(r"\s*\[?expired\]?", title, re.I)) or "[expired]" in title.lower()
    title = re.sub(r"^\s*\[expired\]\s*", "", title, flags=re.I)

    content = ""
    if entry.get("content"):
        content = entry["content"][0].get("value", "")
    content = content or entry.get("summary", "")

    # First outbound "click" link is the store link Slickdeals tracks for the deal.
    store_url = ""
    source = ""
    m = re.search(r'<a href="(https://slickdeals\.net/click\?[^"]+)"[^>]*>([^<]*)</a>', content)
    if m:
        store_url = html.unescape(m.group(1))
        source = html.unescape(m.group(2)).strip()

    t = entry.get("published_parsed") or entry.get("updated_parsed")
    published = datetime(*t[:6], tzinfo=timezone.utc) if t else None

    return Deal(
        title=title,
        thread_url=entry.get("link", ""),
        store_url=store_url or entry.get("link", ""),
        published=published,
        expired=expired,
        price=first_price(title),
        source=source,
    )


def fetch(query: str) -> list[Deal] | Exception:
    url = SLICKDEALS_SEARCH.format(query=query)
    try:
        feed = feedparser.parse(url)
    except Exception as e:  # pragma: no cover - network
        return e
    if feed.bozo and not feed.entries:
        return Exception(str(getattr(feed, "bozo_exception", "feed error")))
    deals = [parse_entry(e) for e in feed.entries]
    cutoff_hours = MAX_AGE_DAYS * 24
    fresh = []
    for d in deals:
        if d.expired:
            continue
        if d.published and (datetime.now(timezone.utc) - d.published).total_seconds() > cutoff_hours * 3600:
            continue
        fresh.append(d)
    return fresh


def print_section(header: str, browse_label: str, browse_url: str, deals: list[Deal] | Exception) -> None:
    print(header)
    print(f"--{browse_label} | href={browse_url}")
    if isinstance(deals, Exception):
        print(f"--Feed error | color=red")
        print(f"--{trim(str(deals), 100)} | size=11")
        return
    if not deals:
        print("--No current deals")
        return
    for d in deals[:MAX_ITEMS]:
        stamp = f" · {d.age}" if d.age else ""
        tag = f" ({d.source})" if d.source and d.source.lower() not in header.lower() else ""
        label = trim(d.title)
        tooltip = (clean(d.title) + tag).replace('"', "'")
        print(f'--{label}{stamp} | href={d.store_url} tooltip="{tooltip}"')
        print(f"--{label}{stamp}  ↗ Slickdeals | href={d.thread_url} alternate=true")


def main() -> int:
    with ThreadPoolExecutor(max_workers=2) as pool:
        mc_future = pool.submit(fetch, MICROCENTER_QUERY)
        dw_future = pool.submit(fetch, DEWALT_QUERY)
        mc = mc_future.result()
        dw = dw_future.result()

    if not isinstance(mc, Exception):
        # Slickdeals search matches the word anywhere; keep deals actually sold by Micro Center.
        mc = [d for d in mc if re.search(r"micro\s?center", d.source + " " + d.title + " " + d.store_url, re.I)]

    if not isinstance(dw, Exception):
        # Keep only DeWalt deals that are actually at Lowe's, power tools first.
        dw = [d for d in dw if "lowe" in (d.source + " " + d.title).lower()]
        dw.sort(key=lambda d: (not d.is_power_tool, -(d.published.timestamp() if d.published else 0)))

    print("💻")
    print("---")
    print_section("Micro Center", "Micro Center Top Deals ↗", MICROCENTER_STORE_URL, mc)
    print("---")
    print_section("DeWalt @ Lowe's", "Browse DeWalt power tools at Lowe's ↗", LOWES_DEWALT_URL, dw)
    print("---")
    print(f"Updated {datetime.now().strftime('%-m/%-d %-I:%M %p')} · source: Slickdeals | size=11 color=gray")
    print("Refresh | refresh=true")
    return 0


if __name__ == "__main__":
    sys.exit(main())
