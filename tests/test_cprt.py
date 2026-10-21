"""Tests for cprt.1h.py against trimmed real Invision RSS items.

Run:  uv run --with pytest pytest tests/test_cprt.py
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location("cprt", ROOT / "cprt.1h.py")
cprt = importlib.util.module_from_spec(spec)
sys.modules["cprt"] = cprt  # dataclasses resolve string annotations via sys.modules
spec.loader.exec_module(cprt)


def rss(*items):
    return (
        '<?xml version="1.0" encoding="UTF-8" ?>\n<rss version="2.0"><channel>'
        "<title>Feed</title><link>https://chiefpilotsforum.com/</link>"
        + "".join(items)
        + "</channel></rss>"
    )


def item(title, link, desc="", date="Thu, 15 Oct 2026 15:18:36 +0000"):
    return (
        f"<item><title>{title}</title><link><![CDATA[{link}]]></link>"
        f"<description><![CDATA[{desc}]]></description>"
        f"<pubDate>{date}</pubDate></item>"
    )


T = "https://chiefpilotsforum.com/forums/topic/"

# Forum "Latest Topics" feed: topic links, HTML excerpts
LATEST = rss(
    item("Saudi / UAE Routing", T + "1957-saudi-uae-routing/",
         "<p>As luck would have it, we have a few <b>upcoming</b> trips&hellip;</p>"),
    item("QOL discussion", T + "1945-qol-discussion/", "<p>QOL questions:</p>",
         "Wed, 26 Aug 2026 17:02:11 +0000"),
    item("Galapagos trip", T + "732-galapagos-trip/", "Any info appreciated",
         "Thu, 06 Oct 2016 12:00:00 +0000"),
)

# "Content I have posted in" stream: links point at the latest comment
POSTED = rss(
    item("Saudi / UAE Routing", T + "1957-saudi-uae-routing/?do=findComment&comment=42418",
         "No help on this one."),
    item("Galapagos trip", T + "732-galapagos-trip/?do=findComment&comment=42413", "Sorry"),
)


def test_parse_feed_strips_comment_suffix_and_html():
    topics = cprt.parse_feed(POSTED)
    assert [t.id for t in topics] == ["1957", "732"]
    assert topics[0].url == T + "1957-saudi-uae-routing/"
    latest = cprt.parse_feed(LATEST)
    assert latest[0].excerpt == "As luck would have it, we have a few upcoming trips…"
    assert latest[1].date.startswith("2026-08-26T17:02:11")


def test_parse_feed_dedupes_reply_level_items():
    feed = rss(
        item("Saudi / UAE Routing", T + "1957-saudi-uae-routing/?do=findComment&comment=2"),
        item("Saudi / UAE Routing", T + "1957-saudi-uae-routing/?do=findComment&comment=1"),
    )
    assert [t.id for t in cprt.parse_feed(feed)] == ["1957"]


def test_parse_feed_rejects_cloudflare_page():
    with pytest.raises(Exception):
        cprt.parse_feed("<!DOCTYPE html><html><head><title>Just a moment...</title>")


def test_owed_is_latest_minus_posted_in_feed_order():
    owed = cprt.owed_topics(cprt.parse_feed(LATEST), cprt.parse_feed(POSTED))
    assert [t.title for t in owed] == ["QOL discussion"]


def test_render_lists_owed_and_replied():
    feeds = {"latest": cprt.parse_feed(LATEST), "posted": cprt.parse_feed(POSTED)}
    out = cprt.render(feeds, None, None).splitlines()
    assert out[0].endswith(" 1 | sfsize=14")
    assert f"QOL discussion | href={T}1945-qol-discussion/ tooltip='QOL questions:'" in out
    assert "Replied (2)" in out
    assert not any("Stale" in line for line in out)


def test_render_caught_up():
    feeds = {"latest": cprt.parse_feed(POSTED), "posted": cprt.parse_feed(POSTED)}
    out = cprt.render(feeds, None, None).splitlines()
    assert out[0] == ":checkmark.bubble: | sfsize=14"
    assert "All caught up · last 2 topics | color=gray" in out


def test_tooltip_and_title_are_swiftbar_safe():
    t = cprt.Topic(id="1", title="A | B " + "x" * 80, url=T + "1-a/", date="",
                   excerpt="it's a | pipe")
    line = cprt.topic_line(t)
    title, params = line.split(" | ", 1)
    assert "|" not in title and title.endswith("…")
    assert params == f"href={T}1-a/ tooltip='it’s a / pipe'"


def test_failed_posted_feed_falls_back_to_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(cprt, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(cprt.time, "sleep", lambda s: None)
    pages = {"latest": LATEST, "posted": POSTED}
    monkeypatch.setattr(cprt, "feed_url", lambda name: name)
    monkeypatch.setattr(cprt, "fetch", lambda url: pages[url])
    feeds, error, _ = cprt.load()
    assert error is None and len(feeds["posted"]) == 2

    def fail_posted(url):
        if url == "posted":
            raise OSError("HTTP Error 429")
        return pages[url]

    monkeypatch.setattr(cprt, "fetch", fail_posted)
    feeds, error, fetched = cprt.load()
    assert error == "posted: HTTP Error 429"
    assert len(feeds["posted"]) == 2  # cached pair, not "everything owed"
    assert any("Stale" in line for line in cprt.render(feeds, error, fetched).splitlines())


def test_no_cache_and_no_config_renders_error(monkeypatch, tmp_path):
    monkeypatch.setattr(cprt, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(cprt, "feed_url", lambda name: None)
    feeds, error, fetched = cprt.load()
    assert feeds is None
    out = cprt.render(feeds, error, fetched)
    assert "CPRT feeds unavailable" in out and "CPRT_FEED_URL not configured" in out
