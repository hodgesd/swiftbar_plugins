#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

# <swiftbar.title>CPRT Replies Owed</swiftbar.title>
# <swiftbar.version>v2.0</swiftbar.version>
# <swiftbar.author>Derrick Hodges</swiftbar.author>
# <swiftbar.author.github>hodgesd</swiftbar.author.github>
# <swiftbar.desc>Chief Pilots Roundtable topics you haven't posted in yet</swiftbar.desc>
# <swiftbar.dependencies>uv</swiftbar.dependencies>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideLastUpdated>false</swiftbar.hideLastUpdated>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>

"""
CPRT Replies Owed — SwiftBar Plugin
===================================
The Chief Pilots Roundtable expects every member to reply to every topic.
This lists the recent topics you haven't posted in. It only reads two RSS
feeds; it never logs in and cannot post.

Feeds (Invision Community, keyed by member id + private feed key):
  * latest  — the forum's "Latest Topics" RSS
              (/forums/forum/5-chief-pilots-roundtable.xml/?member=…&key=…)
  * posted  — an Activity Stream RSS with Content Types = Topics, Ownership =
              "Content I have posted in", Show me = "Content items only"
              (/discover/<id>.xml/?member=…&key=…)

Both list 25 topics ordered by last activity, and every topic you've posted in
is also a forum topic, so  owed = latest − posted  is exact within the latest
25. Invision RSS carries no author field, which is why the second feed exists.

The URLs hold a private key, so they live outside this public repo:
  CPRT_FEED_URL / CPRT_FEED_URL_FILE (default /run/secrets/cprt-feed-url)
  CPRT_POSTED_FEED_URL / CPRT_POSTED_FEED_URL_FILE
      (default /run/secrets/cprt-posted-feed-url)

The last good result is cached in ~/.cache/swiftbar-plugins/cprt/ and shown,
marked stale, when a fetch fails (Cloudflare rate-limits bursts with 429).
Runs within 5 minutes of the last fetch attempt (wake, Refresh clicks) replay
the cache instead of hitting the forum, so it's two requests per run, at most
two per 5 minutes, and normally two per hour.

  ./cprt.1h.py --check   prints per-feed counts, exits 1 on any failure
"""

import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

FORUM_URL = "https://chiefpilotsforum.com/forums/forum/5-chief-pilots-roundtable/"
UNREAD_URL = "https://chiefpilotsforum.com/discover/unread/"
CACHE_DIR = os.path.expanduser("~/.cache/swiftbar-plugins/cprt")
CACHE_FILE = "last_good.json"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
TITLE_LEN = 55
TOOLTIP_LEN = 300
ATTEMPT_FILE = "last_attempt.json"
MIN_FETCH_INTERVAL = 300  # seconds between fetches, however often SwiftBar runs us

FEEDS = {
    "latest": ("CPRT_FEED_URL", "/run/secrets/cprt-feed-url"),
    "posted": ("CPRT_POSTED_FEED_URL", "/run/secrets/cprt-posted-feed-url"),
}


@dataclass
class Topic:
    id: str
    title: str
    url: str
    date: str  # ISO 8601; topic start date (latest) or last comment (posted)
    excerpt: str


# ─── Config ──────────────────────────────────────────────────────────────

def feed_url(name):
    env, default_file = FEEDS[name]
    url = os.environ.get(env, "").strip()
    if url:
        return url
    try:
        with open(os.environ.get(f"{env}_FILE", default_file), encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


# ─── Parsing ─────────────────────────────────────────────────────────────

class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def strip_html(html):
    p = _TextExtractor()
    p.feed(html or "")
    return " ".join("".join(p.parts).split())


TOPIC_RE = re.compile(r"/forums/topic/(\d+)(-[^/?#]*)?")


def topic_id(link):
    m = TOPIC_RE.search(link or "")
    return m.group(1) if m else None


def topic_url(link):
    """Topic URL without the ?do=findComment&comment=N suffix stream feeds add."""
    m = TOPIC_RE.search(link or "")
    if not m:
        return link
    return link[: m.end()] + "/"


def parse_feed(xml_text):
    """Topics from an Invision RSS feed, one per topic id, in feed order."""
    root = ET.fromstring(xml_text)
    if root.find("channel") is None:
        raise ValueError("not an RSS feed")
    topics, seen = [], set()
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        tid = topic_id(link)
        if not tid or tid in seen:
            continue
        seen.add(tid)
        try:
            date = parsedate_to_datetime(item.findtext("pubDate") or "").isoformat()
        except (TypeError, ValueError):
            date = ""
        topics.append(Topic(
            id=tid,
            title=" ".join((item.findtext("title") or "Untitled").split()),
            url=topic_url(link),
            date=date,
            excerpt=strip_html(item.findtext("description")),
        ))
    return topics


def owed_topics(latest, posted):
    mine = {t.id for t in posted}
    return [t for t in latest if t.id not in mine]


# ─── Fetching and cache ──────────────────────────────────────────────────

def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_topics(name):
    url = feed_url(name)
    if not url:
        raise LookupError(f"{FEEDS[name][0]} not configured")
    return parse_feed(fetch(url))


def read_cache():
    try:
        with open(os.path.join(CACHE_DIR, CACHE_FILE), encoding="utf-8") as f:
            data = json.load(f)
        return {k: [Topic(**t) for t in data[k]] for k in FEEDS}, data["fetched"]
    except (OSError, ValueError, KeyError, TypeError):
        return None, None


def write_cache(feeds):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, CACHE_FILE)
    data = {k: [asdict(t) for t in v] for k, v in feeds.items()}
    data["fetched"] = time.time()
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(path + ".tmp", path)


def read_attempt():
    try:
        with open(os.path.join(CACHE_DIR, ATTEMPT_FILE), encoding="utf-8") as f:
            data = json.load(f)
        return float(data["at"]), data.get("error")
    except (OSError, ValueError, KeyError, TypeError):
        return 0.0, None


def write_attempt(error):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(os.path.join(CACHE_DIR, ATTEMPT_FILE), "w", encoding="utf-8") as f:
        json.dump({"at": time.time(), "error": error}, f)


def load():
    """Return (feeds, error, fetched_ts). Both feeds must succeed together;
    otherwise fall back to the last good pair so a failed "posted" fetch
    never makes every topic look unanswered.

    SwiftBar also reruns plugins on wake and on Refresh, so fetches (good or
    failed) are at least MIN_FETCH_INTERVAL apart; in between, the last
    result is replayed from cache."""
    attempted, last_error = read_attempt()
    if time.time() - attempted < MIN_FETCH_INTERVAL:
        cached, fetched = read_cache()
        if cached is not None or last_error:
            return cached, last_error, fetched
    stage = "latest"
    try:
        feeds = {}
        for i, stage in enumerate(FEEDS):
            if i:
                time.sleep(1)  # Cloudflare 429s quick bursts
            feeds[stage] = fetch_topics(stage)
        if not feeds["latest"]:
            raise ValueError("latest-topics feed is empty")
        write_cache(feeds)
        write_attempt(None)
        return feeds, None, time.time()
    except Exception as e:  # noqa: BLE001 — any failure degrades to cache
        error = f"{stage}: {e or e.__class__.__name__}"
        write_attempt(error)
        cached, fetched = read_cache()
        return cached, error, fetched


# ─── Rendering ───────────────────────────────────────────────────────────

def clean(text):
    """Make text safe for a SwiftBar line (| starts parameters)."""
    return " ".join((text or "").replace("|", "/").split())


def short_date(iso):
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%b %-d")
    except ValueError:
        return ""


def topic_line(t, prefix=""):
    title = clean(t.title)
    if len(title) > TITLE_LEN:
        title = title[: TITLE_LEN - 1] + "…"
    tip = clean(t.excerpt)[:TOOLTIP_LEN]
    params = f"href={t.url}"
    if tip:
        params += f" tooltip='{tip.replace(chr(39), '’')}'"
    return f"{prefix}{title} | {params}"


def render(feeds, error, fetched):
    lines = []
    if feeds is None:
        lines += [
            ":exclamationmark.bubble: | sfsize=14",
            "---",
            "CPRT feeds unavailable | color=red",
            f"-- {clean(error)} | color=gray",
        ]
    else:
        owed = owed_topics(feeds["latest"], feeds["posted"])
        replied = [t for t in feeds["latest"] if t not in owed]
        if owed:
            lines.append(f":bubble.left.and.exclamationmark.bubble.right: {len(owed)} | sfsize=14")
        else:
            lines.append(":checkmark.bubble: | sfsize=14")
        lines.append("---")
        if owed:
            lines.append(f"Replies owed ({len(owed)}) | size=12 color=gray")
            for t in owed:
                lines.append(topic_line(t))
                started = short_date(t.date)
                if started:
                    lines.append(f"-- Started {started} | color=gray")
        else:
            lines.append(f"All caught up · last {len(feeds['latest'])} topics | color=gray")
        lines.append("---")
        lines.append(f"Replied ({len(replied)})")
        for t in replied:
            lines.append(topic_line(t, prefix="--"))
        if error:
            age = datetime.fromtimestamp(fetched).strftime("%b %-d %-I:%M %p") if fetched else "?"
            lines.append("---")
            lines.append(f"⚠ Stale (from {age}) | color=orange")
            lines.append(f"-- {clean(error)} | color=gray")
    lines += [
        "---",
        f"Open Roundtable | href={FORUM_URL}",
        f"Unread Content | href={UNREAD_URL}",
        "Refresh | refresh=true",
    ]
    return "\n".join(lines)


def check():
    ok = True
    results = {}
    for i, name in enumerate(FEEDS):
        if i:
            time.sleep(1)
        try:
            results[name] = fetch_topics(name)
            print(f"{name:7} OK   {len(results[name])} topics")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"{name:7} FAIL {e}")
    if ok:
        owed = owed_topics(results["latest"], results["posted"])
        print(f"owed    {len(owed)}: " + ", ".join(t.title for t in owed))
    return 0 if ok else 1


def main():
    if "--check" in sys.argv:
        sys.exit(check())
    print(render(*load()))


if __name__ == "__main__":
    main()
