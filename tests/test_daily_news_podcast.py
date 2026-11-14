"""The ⌥ "Podcast ·" headline rows and the --podcast send in daily_news_uv.2h.py.

Run:  uv run --with pytest --with aiohttp --with beautifulsoup4 --with feedparser \
        --with requests pytest tests/test_daily_news_podcast.py
"""
import importlib.util
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location("daily_news", ROOT / "daily_news_uv.2h.py")
dn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dn)

ARTICLE = "https://www.nytimes.com/2026/11/13/technology/anthropic-ipo-spending-plans.html"


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PODCAST_INTAKE_TOKEN", "test-token")
    dn.podcast_token.cache_clear()
    yield "test-token"
    dn.podcast_token.cache_clear()


@pytest.fixture
def no_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("PODCAST_INTAKE_TOKEN", raising=False)
    monkeypatch.setattr(dn, "PODCAST_TOKEN_FILE", str(tmp_path / "missing"))
    dn.podcast_token.cache_clear()
    yield
    dn.podcast_token.cache_clear()


def test_headline_is_followed_by_its_podcast_alternate(token: str) -> None:
    headline, alternate, end = dn.format_headline("Wall Street Braces", ARTICLE).split("\n")
    assert headline.startswith("-- Wall Street Braces | href=" + ARTICLE)
    assert alternate == (
        f'-- Podcast · Wall Street Braces | bash="{dn.PLUGIN_PATH}" param1=--podcast '
        f'param2="{ARTICLE}" terminal=false sfimage=headphones alternate=true trim=false'
    )
    assert end == ""


def test_discussion_rows_send_the_article_not_the_thread(token: str) -> None:
    row = dn.format_headline("Story", "https://news.ycombinator.com/item?id=1", podcast_url=ARTICLE)
    assert f'param2="{ARTICLE}"' in row


def test_no_alternate_without_a_token_or_a_usable_url(token: str, monkeypatch, tmp_path) -> None:
    assert dn.podcast_line("Ask HN", None) == ""
    assert dn.podcast_line("Odd", 'https://example.com/a "b"') == ""
    monkeypatch.delenv("PODCAST_INTAKE_TOKEN")
    monkeypatch.setattr(dn, "PODCAST_TOKEN_FILE", str(tmp_path / "missing"))
    dn.podcast_token.cache_clear()
    assert dn.format_headline("Story", ARTICLE).count("\n") == 1


class Reply:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code, self.text = status_code, text


def capture(monkeypatch: pytest.MonkeyPatch, reply) -> tuple[list, list]:
    posts, notes = [], []

    def post(url, json, timeout, headers):
        posts.append((url, json, headers))
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(dn.requests, "post", post)
    monkeypatch.setattr(dn, "notify", lambda title, message: notes.append((title, message)))
    return posts, notes


def test_send_queues_the_url_and_says_so(token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    posts, notes = capture(monkeypatch, Reply(202, '{"id": "x", "status": "queued"}'))
    assert dn.send_to_podcast(ARTICLE) == 0
    assert posts == [(dn.PODCAST_INTAKE_URL, {"url": ARTICLE}, {"Authorization": "Bearer test-token"})]
    assert notes == [("Queued for Read Later", "nytimes.com · Overcast alerts when it's ready")]


def test_send_failures_notify_and_exit_1(token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _, notes = capture(monkeypatch, Reply(401, '{"error": "missing or wrong bearer token"}'))
    assert dn.send_to_podcast(ARTICLE) == 1
    assert notes[-1][1].startswith("The intake answered 401")
    _, notes = capture(monkeypatch, requests.ConnectionError("no route"))
    assert dn.send_to_podcast(ARTICLE) == 1
    assert notes[-1][1].startswith("Intake unreachable")


def test_send_without_a_token_does_not_post(no_token, monkeypatch: pytest.MonkeyPatch) -> None:
    posts, notes = capture(monkeypatch, Reply(202))
    assert dn.send_to_podcast(ARTICLE) == 1
    assert posts == [] and notes[0][0] == "Not sent to Read Later"


def test_google_news_links_get_no_podcast_row(token: str) -> None:
    link = "https://news.google.com/rss/articles/CBMifkFVX3lx?oc=5"
    assert dn.format_headline("BND story", link).count("\n") == 1


def test_youtube_links_get_no_podcast_row(token: str) -> None:
    for link in ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ"):
        assert dn.format_headline("A video", link).count("\n") == 1
