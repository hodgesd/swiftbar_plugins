#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "feedparser>=6.0.0",
#     "curl_cffi>=0.7.0",
# ]
# ///

# <swiftbar.title>Deals: Micro Center + DeWalt</swiftbar.title>
# <swiftbar.version>v2.1</swiftbar.version>
# <swiftbar.author>Derrick Hodges</swiftbar.author>
# <swiftbar.author.github>hodgesd</swiftbar.author.github>
# <swiftbar.desc>Current Micro Center deals and DeWalt power-tool deals at Lowe's, via Slickdeals. Rows show [price −savings%] title · category · age. Click opens the store; ⌥-click opens the Slickdeals thread.</swiftbar.desc>
# <swiftbar.dependencies>uv, feedparser, curl_cffi</swiftbar.dependencies>

# v2.0 (2026-10-11): microcenter.com now sits behind a Cloudflare JS challenge and
# Bing Shopping no longer exposes sale prices, so both sections come from
# Slickdeals search RSS instead. Kobalt replaced by DeWalt power tools.
# v2.1 (2026-10-12): each thread page's GTM dataLayer carries finalPrice,
# listPrice, subCategory and an expired flag; fetched concurrently and cached
# for a day under ~/.cache/swiftbar-plugins so rows can lead with [$price −NN%]
# and end with the category.

from __future__ import annotations

import html
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import feedparser

SLICKDEALS_SEARCH = "https://slickdeals.net/newsearch.php?q={query}&rss=1"
MICROCENTER_QUERY = "microcenter"
DEWALT_QUERY = "dewalt+lowes"
MICROCENTER_STORE_URL = "https://www.microcenter.com/site/content/top-deals.aspx"
LOWES_DEWALT_URL = "https://www.lowes.com/pl/power-tools/dewalt/4294612237"

MAX_ITEMS = 12
MAX_AGE_DAYS = 14
MAX_TITLE = 64
THREAD_FETCH_TIMEOUT = 15
FEED_FETCH_TIMEOUT = 15
THREAD_FETCH_WORKERS = 6
CACHE_PATH = Path.home() / ".cache" / "swiftbar-plugins" / "deals_threads.json"
CACHE_TTL = 24 * 3600

# Rough "is this a power tool" signal; matches rank above bits, blades and accessories.
POWER_TOOL_WORDS = re.compile(
    r"\b(drill|driver|impact|hammer|saw|sander|grinder|router|planer|jigsaw|"
    r"nailer|stapler|trimmer|blower|mower|chainsaw|vacuum|vac|compressor|"
    r"combo kit|kit|battery|batteries|charger|flashlight|light|inflator|"
    r"multi-?tool|oscillating tool|pressure washer|shear|polisher|heat gun|"
    r"ratchet|wrench|cut-?off|tool only|brushless|20v|60v|flexvolt|xr)s?\b",
    re.I,
)
ACCESSORY_WORDS = re.compile(
    r"\b(bit set|bit|blade|holder|extension|case|bag|socket|screwdriver|"
    r"knife|tape measure|glove|pencil|marker|sandpaper|disc|abrasive|hose|"
    r"ear|safety|level|clamp|knee)s?\b",
    re.I,
)
POWER_TOOL_CATEGORIES = ("Drills & Power Tools", "Power Tools", "Outdoor Power")

# Slickdeals categories are long; shorten the common ones for the menu row.
CATEGORY_ALIASES = {
    "Drills & Power Tools": "Power Tools",
    "Home & Home Improvement": "Home",
    "Computer Components": "Components",
    "Computer Accessories": "Accessories",
    "Video Cards & GPUs": "GPUs",
    "Solid State Drives (SSD)": "SSDs",
    "Memory (RAM)": "RAM",
    "Desktop Computers": "Desktops",
    "Monitors & Displays": "Monitors",
    "Outdoor Power Equipment": "Outdoor Power",
}


@dataclass
class Deal:
    title: str
    thread_id: str
    thread_url: str
    store_url: str
    published: Optional[datetime]
    expired: bool
    price: Optional[float]
    source: str  # store name as Slickdeals reported it
    list_price: Optional[float] = None
    category: str = ""
    categories: list[str] = field(default_factory=list)

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
    def savings_pct(self) -> Optional[int]:
        if self.price and self.list_price and self.list_price > self.price:
            return round((self.list_price - self.price) / self.list_price * 100)
        return None

    @property
    def price_tag(self) -> str:
        if not self.price:
            return ""
        tag = f"${round(self.price):,}"
        if self.savings_pct:
            tag += f" −{self.savings_pct}%"
        return f"[{tag}]"

    @property
    def is_power_tool(self) -> bool:
        # Title keywords decide; Slickdeals files bits and sockets under
        # "Drills & Power Tools" too, so the category alone is not enough.
        return bool(POWER_TOOL_WORDS.search(self.title)) and not (
            ACCESSORY_WORDS.search(self.title) and not re.search(r"combo|kit|tool only", self.title, re.I)
        )

    @property
    def in_power_tool_category(self) -> bool:
        return any(c in self.categories for c in POWER_TOOL_CATEGORIES)


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


def strip_price(title: str) -> str:
    """Drop the price (and a leading store tag) from the title since the row carries both."""
    t = re.sub(r"^\s*(?:\[?\s*micro\s?center\s*\]?\s*:?\s*|lowe'?s\s*:?\s*)", "", title, flags=re.I)
    not_discount = r"(?![\d,.]*\*?\s*(?:off|discount|rebate|gift|credit)\b)"
    t = re.sub(rf"^\s*\${not_discount}[\d,]+(?:\.\d{{1,2}})?\*?\s*(?:[:⎮|-]\s*)?", "", t, flags=re.I)
    t = re.sub(
        rf"\s*(?:for|@|-|–)?\s*\${not_discount}[\d,]+(?:\.\d{{1,2}})?\*?(\s*\+\s*free[^,(]*)?",
        "", t, count=1, flags=re.I,
    )
    t = re.sub(r"\s{2,}", " ", t).strip(" -–:⎮")
    return t or title


def parse_entry(entry) -> Deal:
    title = clean(entry.get("title", "(no title)"))
    expired = "[expired]" in title.lower()
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

    link = entry.get("link", "")
    tid = re.search(r"/f/(\d+)", link)

    return Deal(
        title=title,
        thread_id=tid.group(1) if tid else link,
        thread_url=link,
        store_url=store_url or link,
        published=published,
        expired=expired,
        price=first_price(title),
        source=source,
    )


def fetch(query: str) -> list[Deal] | Exception:
    url = SLICKDEALS_SEARCH.format(query=query)
    try:
        from curl_cffi import requests as creq

        r = creq.get(url, impersonate="chrome", timeout=FEED_FETCH_TIMEOUT)
        r.raise_for_status()
        feed = feedparser.parse(r.text)
    except Exception as e:  # pragma: no cover - network
        return e
    if feed.bozo and not feed.entries:
        return Exception(str(getattr(feed, "bozo_exception", "feed error")))
    deals = [parse_entry(e) for e in feed.entries]
    cutoff = MAX_AGE_DAYS * 24 * 3600
    fresh = []
    for d in deals:
        if d.expired:
            continue
        if d.published and (datetime.now(timezone.utc) - d.published).total_seconds() > cutoff:
            continue
        fresh.append(d)
    return fresh


# --- thread details (price, list price, category) -------------------------------

def load_cache() -> dict:
    try:
        data = json.loads(CACHE_PATH.read_text())
        now = time.time()
        return {k: v for k, v in data.items() if now - v.get("at", 0) < CACHE_TTL}
    except Exception:
        return {}


def save_cache(cache: dict) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache))
        os.replace(tmp, CACHE_PATH)
    except Exception:
        pass


def _money(s) -> Optional[float]:
    if s is None:
        return None
    try:
        return float(str(s).replace(",", "").replace("$", ""))
    except ValueError:
        return None


def parse_thread_html(page: str) -> dict:
    """Pull the deal fields out of the GTM dataLayer embedded in a thread page."""
    out: dict = {}
    m = re.search(r'"finalPrice":(null|"[^"]*")\s*,\s*"listPrice":(null|"[^"]*")', page)
    if m:
        out["price"] = _money(json.loads(m.group(1)))
        out["list_price"] = _money(json.loads(m.group(2)))
    m = re.search(r'"subCategory":(null|"(?:[^"\\]|\\.)*")', page)
    if m:
        out["sub_category"] = json.loads(m.group(1)) or ""
    m = re.search(r'"primaryCategory":(null|"(?:[^"\\]|\\.)*")', page)
    if m:
        out["primary_category"] = json.loads(m.group(1)) or ""
    m = re.search(r'"category":"((?:[^"\\]|\\.)*)"', page)
    if m:
        out["categories"] = [c for c in json.loads(f'"{m.group(1)}"').split("|") if c]
    m = re.search(r'"isExpiredDeal":"(Yes|No)"', page)
    if m:
        out["expired"] = m.group(1) == "Yes"
    return out


def fetch_thread(url: str) -> dict:
    from curl_cffi import requests as creq

    try:
        r = creq.get(url, impersonate="chrome", timeout=THREAD_FETCH_TIMEOUT)
        if r.status_code != 200:
            return {}
        return parse_thread_html(r.text)
    except Exception:
        return {}


def enrich(deals: list[Deal]) -> None:
    """Fill price/list price/category from each thread page, using the day cache."""
    cache = load_cache()
    missing = [d for d in deals if d.thread_id not in cache]
    if missing:
        with ThreadPoolExecutor(max_workers=THREAD_FETCH_WORKERS) as pool:
            for d, info in zip(missing, pool.map(lambda d: fetch_thread(d.thread_url), missing)):
                if info:
                    info["at"] = time.time()
                    cache[d.thread_id] = info
        save_cache(cache)
    for d in deals:
        info = cache.get(d.thread_id)
        if not info:
            continue
        if info.get("price"):
            d.price = info["price"]
        d.list_price = info.get("list_price")
        d.categories = info.get("categories") or []
        d.category = info.get("sub_category") or info.get("primary_category") or ""
        if info.get("expired"):
            d.expired = True


def short_category(name: str) -> str:
    return CATEGORY_ALIASES.get(name, name)


def print_section(header: str, browse_label: str, browse_url: str, deals: list[Deal] | Exception) -> None:
    print(header)
    print(f"--{browse_label} | href={browse_url}")
    if isinstance(deals, Exception):
        print("--Feed error | color=red")
        print(f"--{trim(str(deals), 100)} | size=11")
        return
    if not deals:
        print("--No current deals")
        return
    for d in deals[:MAX_ITEMS]:
        lead = f"{d.price_tag} " if d.price_tag else ""
        label = trim(strip_price(d.title))
        tail = " · ".join(x for x in (short_category(d.category), d.age) if x)
        row = f"--{lead}{label}" + (f" · {tail}" if tail else "")
        tip_parts = [clean(d.title)]
        if d.list_price and d.price and d.list_price > d.price:
            tip_parts.append(f"was ${d.list_price:,.2f}, now ${d.price:,.2f}")
        if d.category:
            tip_parts.append(d.category)
        if d.source and d.source.lower() not in header.lower():
            tip_parts.append(d.source)
        tooltip = " — ".join(tip_parts).replace('"', "'")
        print(f'{row} | href={d.store_url} tooltip="{tooltip}"')
        print(f"{row}  ↗ Slickdeals | href={d.thread_url} alternate=true")


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
        dw = [d for d in dw if "lowe" in (d.source + " " + d.title).lower()]

    enrich([d for lst in (mc, dw) if not isinstance(lst, Exception) for d in lst])

    if not isinstance(mc, Exception):
        mc = [d for d in mc if not d.expired]
    if not isinstance(dw, Exception):
        dw = [d for d in dw if not d.expired]
        # Power tools first, then newest.
        dw.sort(
            key=lambda d: (
                not d.is_power_tool,
                not d.in_power_tool_category,
                -(d.published.timestamp() if d.published else 0),
            )
        )

    if isinstance(mc, Exception) and isinstance(dw, Exception):
        print("| sfimage=exclamationmark.triangle sfcolor=red")
    else:
        print("| sfimage=tag.fill")
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
