#!/usr/bin/env -S uv run
# /// script
# dependencies = [
#   "feedparser",
# ]
# ///

# <xbar.title>Lowe's Deals</xbar.title>
# <xbar.version>v2.0</xbar.version>
# <xbar.author>Daniel Hodges</xbar.author>
# <xbar.desc>Latest Lowe's deals via SlickDeals RSS.</xbar.desc>
# <xbar.dependencies>python,feedparser</xbar.dependencies>

import html
import sys
from datetime import datetime, timezone

import feedparser

FEED_URL = "https://slickdeals.net/newsearch.php?q=lowes&rss=1"
MAX_ITEMS = 10
MAX_TITLE = 80


def trim(text: str, limit: int = MAX_TITLE) -> str:
    text = html.unescape(text).replace("|", "⎮").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def age(entry) -> str:
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    if not t:
        return ""
    dt = datetime(*t[:6], tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    hours = int(delta.total_seconds() // 3600)
    if hours < 1:
        return f"{int(delta.total_seconds() // 60)}m ago"
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def main() -> int:
    print("Lowe's 🏷️")
    print("---")
    try:
        feed = feedparser.parse(FEED_URL)
    except Exception as e:
        print(f"Error fetching feed | color=red")
        print(f"-- {e}")
        return 1

    if feed.bozo and not feed.entries:
        print("Feed error | color=red")
        print(f"-- {feed.bozo_exception}")
        return 1

    entries = feed.entries[:MAX_ITEMS]
    if not entries:
        print("No deals found")
        return 0

    for entry in entries:
        title = trim(entry.get("title", "(no title)"))
        link = entry.get("link", "")
        stamp = age(entry)
        suffix = f" · {stamp}" if stamp else ""
        print(f"{title}{suffix} | href={link}")

    print("---")
    print(f"SlickDeals: lowes | href={FEED_URL.rsplit('&', 1)[0]}")
    print("Refresh | refresh=true")
    return 0


if __name__ == "__main__":
    sys.exit(main())
