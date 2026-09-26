#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "aiohttp>=3.8.0",
#     "beautifulsoup4>=4.9.0",
#     "feedparser>=6.0.0",
#     "requests>=2.25.0",
# ]
# ///

# <swiftbar.title>Combined Tech News</swiftbar.title>
# <swiftbar.version>v2.2</swiftbar.version>
# <swiftbar.author>Derrick Hodges</swiftbar.author>
# <swiftbar.author.github>hodgesd</swiftbar.author.github>
# <swiftbar.desc>Combines STLToday, STL PR, BND, Techmeme, Lobste.rs, Hacker News, Simon Willison, and the Local & Agentic AI, Home Lab, NBA, EV/Solar and Fitness 50+ topics in one dropdown</swiftbar.desc>
# <swiftbar.dependencies>uv, beautifulsoup4, aiohttp, requests</swiftbar.dependencies>

import asyncio
import calendar
import datetime
import functools
import re
from dataclasses import dataclass
from typing import Optional

import aiohttp
import feedparser
import requests
from aiohttp import ClientTimeout
from bs4 import BeautifulSoup, Tag
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit

# Cache directory for HN comment summaries. A story's file holds either a summary (from HN
# Companion or the llm path) or a miss marker: the llm path failed for it recently.
CACHE_DIR = os.path.expanduser("~/.cache/swiftbar_hn_summaries")
os.makedirs(CACHE_DIR, exist_ok=True)

# ── Discussion summaries via the llm CLI ─────────────────────────────────────────────────
# Only the front-page section pays for a summary, and only for stories that missed both the
# local cache and HN Companion. Settings live in ~/.config/swiftbar-plugins/daily_news.json
# (optional; every key has a default), the same convention as software_versions.2h.py:
#   "summaries"          true                                   false turns the llm path off
#   "summary_model"      "openrouter/openai/gpt-5-mini"         passed to `llm -m`
#   "summary_options"    {"reasoning_effort": "minimal",        passed as `-o key value`; max_tokens
#                         "max_tokens": 4096}                   covers reasoning + reply, and keeps
#                                                               OpenRouter's per-call credit hold small
#   "llm_path"           `llm` on PATH, else ~/.local/bin/llm   SwiftBar's PATH lacks ~/.local/bin
#   "llm_timeout"        20                                     seconds per call
#   "thread_char_budget" 80000                                  thread text per story (~20k tokens)
# DAILY_NEWS_SUMMARY_MODEL in the environment overrides summary_model (bake-offs), and
# DAILY_NEWS_DEBUG=1 prints per-story diagnostics to stderr.
CONFIG_PATH = os.path.expanduser("~/.config/swiftbar-plugins/daily_news.json")
DEBUG = os.environ.get("DAILY_NEWS_DEBUG") == "1"

HN_LLM_CONCURRENCY = 3               # llm processes in flight at once
HN_LLM_SECTION_BUDGET = 60           # seconds the Hacker News section may spend on llm calls
HN_LLM_MAX_CONSECUTIVE_TIMEOUTS = 3  # then stop trying for this run (bad connection)
HN_LLM_MISS_TTL_HOURS = 6            # a failed story is not retried every refresh while on the front page
HN_LLM_MIN_COMMENTS = 3              # fewer is not a discussion worth a paid call
HN_COMMENT_MAX_CHARS = 600
HN_STORY_TEXT_MAX_CHARS = 1500       # Ask HN / Tell HN body text
ALGOLIA_ITEM_URL = "https://hn.algolia.com/api/v1/items/"

# The tooltip code expects exactly this shape: format_hn_tooltip splits paragraphs on blank
# lines and fit_tooltip_lines drops whole trailing "• " bullets first, so the model is asked
# for the layout condense_hncompanion_summary produces. Both sources then render identically.
SUMMARY_INSTRUCTION = (
    "Summarize the Hacker News discussion above for a short tooltip. Output exactly this, as "
    "plain text: one overview paragraph of 2-3 sentences; a blank line; then 3-5 bullet lines, "
    "each formatted \"• Theme — one sentence\" (a 2-5 word theme, an em dash, one sentence). "
    "Cover the key insights and the main disagreements. No headers, no bold, no markdown, no "
    "preamble, nothing after the bullets."
)


def debug(msg: str) -> None:
    if DEBUG:
        print(f"[daily_news] {msg}", file=sys.stderr)


def warn(msg: str) -> None:
    print(f"[daily_news] {msg}", file=sys.stderr)


@functools.lru_cache(maxsize=None)
def summary_config() -> dict:
    """Settings for the llm summary path. The config file is optional; defaults work without it."""
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                cfg = json.load(f) or {}
        except Exception as exc:
            warn(f"ignoring unreadable {CONFIG_PATH}: {exc}")
    options = cfg.get("summary_options", {"reasoning_effort": "minimal", "max_tokens": 4096})
    # Never a literal home directory: the plugin runs as a different user on the mini
    llm_path = cfg.get("llm_path") or shutil.which("llm") or "~/.local/bin/llm"
    return {
        "summaries": bool(cfg.get("summaries", True)),
        "summary_model": (os.environ.get("DAILY_NEWS_SUMMARY_MODEL")
                          or str(cfg.get("summary_model", "openrouter/openai/gpt-5-mini"))),
        "summary_options": dict(options) if isinstance(options, dict) else {},
        "llm_path": os.path.expanduser(str(llm_path)),
        "llm_timeout": float(cfg.get("llm_timeout", 20)),
        "thread_char_budget": int(cfg.get("thread_char_budget", 80000)),
    }


def _cache_path(story_id: str) -> str:
    return os.path.join(CACHE_DIR, f"{story_id}.json")


def get_cached_summary(story_id: str) -> Optional[str]:
    """Retrieve cached summary for a HN story. A miss marker carries no summary, so it reads as None."""
    cache_file = _cache_path(story_id)
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r') as f:
                data = json.load(f)
                return data.get('summary')
        except Exception:
            return None
    return None


def save_summary_cache(story_id: str, summary: str) -> None:
    """Save summary to cache (replacing any miss marker)."""
    try:
        with open(_cache_path(story_id), 'w') as f:
            json.dump({
                'story_id': story_id,
                'summary': summary,
                'timestamp': datetime.datetime.now().isoformat()
            }, f)
    except Exception as e:
        pass  # Silently fail on cache write


def save_miss_marker(story_id: str) -> None:
    """Remember that the llm path failed for this story. Never stores a failure as a summary."""
    try:
        with open(_cache_path(story_id), 'w') as f:
            json.dump({
                'story_id': story_id,
                'miss': True,
                'timestamp': datetime.datetime.now().isoformat()
            }, f)
    except Exception:
        pass


def has_fresh_miss_marker(story_id: str) -> bool:
    """True while a miss marker is younger than HN_LLM_MISS_TTL_HOURS. Companion is still tried."""
    path = _cache_path(story_id)
    try:
        age_hours = (time.time() - os.path.getmtime(path)) / 3600
        with open(path) as f:
            data = json.load(f)
    except Exception:
        return False
    return bool(data.get('miss')) and not data.get('summary') and age_hours < HN_LLM_MISS_TTL_HOURS

from io import StringIO
from requests.adapters import HTTPAdapter, Retry

# Article previews. Several feeds ship a headline and nothing else — Lobste.rs' description
# field holds the submitter's note (usually empty), Hugging Face's feed has no summaries at
# all — which left those rows with a tooltip that just repeated the headline. For them we
# fetch the linked page and read its og:description. Hits and misses are both cached on disk,
# so a steady-state run adds no network work.
PREVIEW_CACHE_DIR = os.path.expanduser("~/.cache/swiftbar_news_previews")
os.makedirs(PREVIEW_CACHE_DIR, exist_ok=True)

PREVIEW_TTL_DAYS = 30        # reuse a description we found for this long
PREVIEW_MISS_TTL_DAYS = 7    # don't re-fetch a page that had none
PREVIEW_PRUNE_DAYS = 90      # drop cache files untouched for this long
PREVIEW_CONCURRENCY = 10
PREVIEW_TIMEOUT = 6
PREVIEW_MAX_BYTES = 150_000
PREVIEW_MIN_CHARS = 40
PREVIEW_SHORT_SUMMARY = 80   # a feed summary shorter than this counts as missing

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")

# These answer scripted requests with consent walls, bot checks or 429s — skip the request
SKIP_PREVIEW_HOSTS = ("youtube.com", "youtu.be", "reddit.com", "x.com", "twitter.com")

# Site-wide descriptions that say nothing about the individual article
BOILERPLATE_DESCRIPTIONS = (
    re.compile(r"^We.?re on a journey to advance and democratize artificial intelligence", re.I),
    re.compile(r"^A Blog post by .+ on Hugging Face$", re.I),
)


def _preview_cache_path(url: str) -> str:
    return os.path.join(PREVIEW_CACHE_DIR, hashlib.sha256(url.encode()).hexdigest() + ".json")


def load_preview(url: str):
    """Return (cache_answered, description). description is None for a remembered miss."""
    path = _preview_cache_path(url)
    try:
        age_days = (time.time() - os.path.getmtime(path)) / 86400
        with open(path) as f:
            data = json.load(f)
    except Exception:
        return False, None
    description = data.get("description")
    if age_days > (PREVIEW_TTL_DAYS if description else PREVIEW_MISS_TTL_DAYS):
        return False, None
    return True, description


def save_preview(url: str, description: Optional[str]) -> None:
    try:
        with open(_preview_cache_path(url), "w") as f:
            json.dump({
                "url": url,
                "description": description,
                "fetched": datetime.datetime.now().isoformat(),
            }, f)
    except Exception:
        pass  # Silently fail on cache write


def prune_cache(directory: str, max_age_days: int) -> None:
    """Delete cache files older than max_age_days; both caches grow unbounded otherwise."""
    cutoff = time.time() - max_age_days * 86400
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        path = os.path.join(directory, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            continue


def usable_description(text: str) -> bool:
    return len(text) >= PREVIEW_MIN_CHARS and not any(
        pattern.search(text) for pattern in BOILERPLATE_DESCRIPTIONS
    )


def extract_meta_description(html: str) -> Optional[str]:
    """Pull a per-article description from a page's meta tags, ignoring site boilerplate."""
    soup = BeautifulSoup(html, "html.parser")
    for attrs in ({"property": "og:description"},
                  {"name": "twitter:description"},
                  {"name": "description"}):
        tag = soup.find("meta", attrs=attrs)
        text = re.sub(r"\s+", " ", (tag.get("content") if tag else None) or "").strip()
        if usable_description(text):
            return text

    # Plenty of personal blogs tag nothing at all — half of lobste.rs on a given day. Their
    # opening paragraph is a fair preview, and the boilerplate filter still applies to it.
    for paragraph in soup.find_all("p", limit=8):
        text = re.sub(r"\s+", " ", paragraph.get_text(" ", strip=True)).strip()
        if usable_description(text):
            return text
    return None


# Placeholder text some feeds put in the description slot — worse than no tooltip at all,
# because it displaces the headline fallback (lobste.rs tag feeds say "Comments" on every item)
JUNK_SUMMARIES = frozenset({'comments', 'comment', 'read more', 'continue reading',
                            'link', 'no summary', 'untitled'})


# WordPress appends this to every summary it syndicates, which is noise in a tooltip
FEED_FOOTER_RE = re.compile(r"\s*The post\b.*?\bappeared first on\b.*$", re.I)


def clean_summary(text: str) -> str:
    """Collapse whitespace, drop the WordPress footer, and drop feed placeholder text."""
    text = FEED_FOOTER_RE.sub('', re.sub(r'\s+', ' ', text or '').strip()).strip()
    return '' if text.lower().strip('.: ') in JUNK_SUMMARIES else text


def previewable(url: str) -> bool:
    return bool(url) and url.startswith("http") and not any(
        host in urlsplit(url).netloc for host in SKIP_PREVIEW_HOSTS
    )


async def _fetch_one_preview(session, semaphore, url):
    description = None
    async with semaphore:
        try:
            headers = {"User-Agent": BROWSER_UA, "Accept": "text/html,application/xhtml+xml"}
            async with session.get(url, headers=headers, allow_redirects=True) as response:
                if response.status == 200 and "html" in response.headers.get("content-type", ""):
                    # read() returns only what is already buffered, which on some hosts is one
                    # small chunk that stops short of <head>
                    raw = b""
                    async for chunk in response.content.iter_chunked(16384):
                        raw += chunk
                        if len(raw) >= PREVIEW_MAX_BYTES:
                            break
                    description = extract_meta_description(raw.decode("utf-8", "ignore"))
        except Exception:
            description = None
    save_preview(url, description)
    return url, description


async def fetch_previews(urls) -> dict:
    """Map article URL -> og:description, for the URLs worth previewing. Cached URLs cost nothing."""
    previews = {}
    pending = []
    for url in dict.fromkeys(url for url in urls if previewable(url)):
        answered, description = load_preview(url)
        if answered:
            if description:
                previews[url] = description
        else:
            pending.append(url)

    if not pending:
        return previews

    try:
        timeout = ClientTimeout(total=PREVIEW_TIMEOUT)
        connector = aiohttp.TCPConnector(ssl=False, limit=PREVIEW_CONCURRENCY)
        semaphore = asyncio.Semaphore(PREVIEW_CONCURRENCY)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            results = await asyncio.gather(
                *(_fetch_one_preview(session, semaphore, url) for url in pending)
            )
        previews.update({url: text for url, text in results if text})
    except Exception:
        pass  # A preview is a nicety; never fail a section over one

    return previews

# Height budget for HN tooltips. macOS draws NSMenu tooltips centred on the pointer and
# never shrinks or re-anchors them, so a tooltip taller than ~2x the hovered row's distance
# from the top of the screen is clipped. With the Hacker News section 6th in the menu and
# SwiftBar's tooltip font at 14pt (NSToolTipsFontSize), the first story has room for
# ~19–20 wrapped lines of ~60 chars; 20 favours keeping bullets over margin. Trimming is line-based:
# whole trailing bullets are dropped first, so sentences are never cut mid-way.
HN_TOOLTIP_MAX_LINES = 20
HN_TOOLTIP_WRAP_CHARS = 60


def cap_tooltip(text: str, max_chars: int) -> str:
    """Truncate tooltip text at a word boundary with an ellipsis if it exceeds max_chars."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(' ', 1)[0] + '…'


def estimate_tooltip_lines(text: str, wrap: int = HN_TOOLTIP_WRAP_CHARS) -> int:
    """Approximate the number of rendered tooltip lines after word wrap (blank lines count)."""
    return sum(max(1, -(-len(line) // wrap)) for line in text.split('\n'))


def fit_tooltip_lines(text: str, max_lines: int = HN_TOOLTIP_MAX_LINES) -> str:
    """Trim tooltip text to roughly max_lines: drop whole trailing bullets first, then, if a
    bullet-less summary is still too tall, truncate it at a word boundary."""
    if estimate_tooltip_lines(text) <= max_lines:
        return text

    lines = text.split('\n')
    dropped = 0
    # Reserve one line for the "+N more" marker that replaces the dropped bullets
    while lines and lines[-1].startswith('•') and estimate_tooltip_lines('\n'.join(lines)) + 1 > max_lines:
        lines.pop()
        dropped += 1
    if dropped:
        while lines and not lines[-1].strip():
            lines.pop()
        lines.append(f'… +{dropped} more theme{"s" if dropped > 1 else ""} on HN')
    text = '\n'.join(lines)

    # Fallback for long bullet-less summaries (a model that ignored the format): shave a line at a time
    while estimate_tooltip_lines(text) > max_lines and len(text) > HN_TOOLTIP_WRAP_CHARS:
        text = cap_tooltip(text.rstrip('…'), len(text) - HN_TOOLTIP_WRAP_CHARS)
    return text


def format_hn_tooltip(summary: str) -> str:
    """Format HN discussion summary with multiline tooltip support."""
    # Split into paragraphs
    paragraphs = [p.strip() for p in summary.split('\n\n') if p.strip()]

    if len(paragraphs) <= 1:
        # Single paragraph - just clean it up
        return fit_tooltip_lines(re.sub(r'\s+', ' ', summary).strip())

    # Format each paragraph with clear visual structure. Collapse whitespace per line,
    # not per paragraph, so each "• Theme — ..." bullet keeps its own line in the tooltip.
    formatted_paras = []
    for para in paragraphs:
        lines = [re.sub(r'\s+', ' ', line).strip() for line in para.splitlines()]
        formatted_paras.append('\n'.join(line for line in lines if line))

    # Join paragraphs with double newline for clear separation
    # Note: The actual newlines will be preserved during escaping
    return fit_tooltip_lines('\n\n'.join(formatted_paras))


def condense_hncompanion_summary(md: str) -> str:
    """Condense HN Companion's structured markdown summary into tooltip-sized plain text."""
    def strip_md(text: str) -> str:
        text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)  # [text](url) -> text
        text = text.replace('**', '').replace('`', '')
        return re.sub(r'\s+', ' ', text).strip()

    # Split the markdown into sections keyed by heading
    sections = {}
    current = None
    for line in md.splitlines():
        heading = re.match(r'#+\s+(.+)', line)
        if heading:
            current = heading.group(1).strip().lower()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)

    overview = strip_md(' '.join(sections.get('overview', [])))

    # Themes appear as "*   **Theme Name:** description" (same line) or with the
    # description indented on following lines — handle both
    theme_lines = []
    themes_raw = '\n'.join(sections.get('main themes & key insights', []))
    for match in re.finditer(
        r'^\s*[*-]\s+\*\*(.+?)\*\*:?\s*(.*?)(?=^\s*[*-]\s+\*\*|\Z)',
        themes_raw, re.M | re.S,
    ):
        name = strip_md(match.group(1)).rstrip(':')
        desc = strip_md(match.group(2))
        first_sentence = re.split(r'(?<=[.!?])\s', desc)[0] if desc else ''
        theme_lines.append(f'• {name} — {first_sentence}' if first_sentence else f'• {name}')

    parts = [p for p in (overview, '\n'.join(theme_lines)) if p]
    condensed = '\n\n'.join(parts) if parts else strip_md(md)

    if len(condensed) > 1200:
        condensed = condensed[:1200].rsplit(' ', 1)[0] + '…'
    return condensed


async def fetch_hncompanion_summary(session: aiohttp.ClientSession, story_id: str) -> Optional[str]:
    """Fetch a free cached AI summary from HN Companion. Returns condensed text, or None on miss."""
    try:
        async with session.get(f"{HNCOMPANION_API}{story_id}") as response:
            if response.status != 200:
                return None  # 404 = story not in their cache; expected
            data = await response.json()
            summary = data.get('summary')
            if not summary:
                return None
            return condense_hncompanion_summary(summary)
    except Exception:
        return None


async def fetch_hn_thread(session: aiohttp.ClientSession, story_id: str) -> Optional[dict]:
    """The story and its full comment tree from Algolia's items endpoint; None on any failure."""
    try:
        async with session.get(f"{ALGOLIA_ITEM_URL}{story_id}") as response:
            if response.status != 200:
                return None
            return await response.json()
    except Exception:
        return None


def _comment_text(node: dict) -> str:
    """Plain text of one Algolia node; '' for deleted, dead or empty comments."""
    html = node.get('text')
    if not html or not node.get('author'):
        return ''
    text = re.sub(r'\s+', ' ', BeautifulSoup(html, 'html.parser').get_text(' ', strip=True)).strip()
    if not text or re.fullmatch(r'\[(deleted|dead|flagged|removed)\]', text, re.I):
        return ''
    return text


def count_comments(item: dict) -> int:
    return sum(1 + count_comments(child) for child in item.get('children') or [])


def flatten_hn_thread(item: dict, char_budget: int) -> str:
    """Render an Algolia item tree as indented "author: text" lines under a title header.

    Depth-first order is kept, but the budget is handed out breadth-first: every top-level
    comment first, then their direct replies, then deeper tails while room remains, and a
    reply is only kept when its parent was. A busy thread therefore loses its deep
    sub-arguments before it loses a single top-level take.
    """
    url = item.get('url') or f"{HN_URL}item?id={item.get('id')}"
    header = f"Title: {item.get('title') or 'Untitled'}\nURL: {url}\n"
    story_text = _comment_text(item)
    if story_text:
        header += f"Post: {cap_tooltip(story_text, HN_STORY_TEXT_MAX_CHARS)}\n"
    header += "\nComments:\n"

    nodes = []  # (depth, parent index, rendered line) in depth-first order

    def walk(children, depth, parent):
        for child in children or []:
            text = _comment_text(child)
            if text:
                index = len(nodes)
                indent = '  ' * depth
                nodes.append((depth, parent, f"{indent}{child.get('author')}: "
                                             f"{cap_tooltip(text, HN_COMMENT_MAX_CHARS)}\n"))
                walk(child.get('children'), depth + 1, index)
            else:
                # A deleted comment's replies still belong to the thread: attach them one level up
                walk(child.get('children'), depth, parent)

    walk(item.get('children'), 0, None)

    kept = set()
    used = len(header)
    for wanted in (0, 1, None):  # passes: top-level, first replies, then everything deeper
        for index, (depth, parent, line) in enumerate(nodes):
            if index in kept or (depth != wanted if wanted is not None else depth < 2):
                continue
            if parent is not None and parent not in kept:
                continue
            if used + len(line) > char_budget:
                continue
            kept.add(index)
            used += len(line)

    return header + ''.join(line for index, (_, _, line) in enumerate(nodes) if index in kept)


def sanitize_llm_summary(text: str) -> str:
    """Coerce a model reply into the overview + "• Theme — sentence" bullets the tooltip expects.

    Strips code fences, bold and headers; normalises "-", "*" and numbered bullets to "• ";
    folds wrapped bullet continuations back onto their bullet. '' when nothing usable is left.
    """
    text = (text or '').replace('\r', '')
    text = re.sub(r'^\s*```[\w-]*\s*$', '', text, flags=re.M)
    text = text.replace('**', '')
    overview, bullets = [], []
    for raw in text.split('\n'):
        line = re.sub(r'\s+', ' ', raw).strip()
        line = re.sub(r'^#+\s*', '', line)
        line = re.sub(r'^(overview|summary)\s*:\s*', '', line, flags=re.I)
        if not line:
            continue
        bullet = re.match(r'^(?:[-*•·▪◦]|\d+[.)])\s*(.+)$', line)
        if bullet:
            body = bullet.group(1).strip()
            if ' — ' not in body:
                # "Theme: sentence" / "Theme - sentence" -> "Theme — sentence"
                body = re.sub(r'^(.{2,60}?)\s*(?::\s|\s-{1,2}\s|\s–\s)\s*', r'\1 — ', body, count=1)
            bullets.append(f'• {body}')
        elif not bullets:
            overview.append(line)
        else:
            bullets[-1] = f'{bullets[-1]} {line}'
    parts = [' '.join(overview).strip(), '\n'.join(bullets)]
    condensed = '\n\n'.join(part for part in parts if part)
    if len(condensed) > 1200:
        condensed = condensed[:1200].rsplit(' ', 1)[0] + '…'
    return condensed


class LlmDisabled(Exception):
    """A failure no per-story retry can fix: binary missing, unknown model, auth or credits."""


class LlmOptionRejected(Exception):
    """The model or plugin refused one of summary_options; retry without them."""


def llm_error_text(stderr: str) -> str:
    """The human-readable part of an llm failure: the API's message fields when it dumped a JSON
    error (OpenRouter's run to a few hundred chars of metadata), else the last 300 chars."""
    messages = re.findall(r"""['"]message['"]:\s*['"]([^'"]{6,})['"]""", stderr)
    text = ' | '.join(dict.fromkeys(messages)) if messages else stderr
    return text[-300:] if len(text) > 300 else text


def run_llm(document: str, instruction: str, cfg: dict, timeout: float, options: dict) -> str:
    """One llm call with `document` on stdin and `instruction` as the prompt; the raw reply.

    Raises LlmDisabled, LlmOptionRejected, subprocess.TimeoutExpired, or RuntimeError.
    """
    cmd = [cfg['llm_path'], '-m', cfg['summary_model'], '--no-stream']
    for key, value in sorted(options.items()):
        cmd += ['-o', str(key), str(value)]
    cmd.append(instruction)
    try:
        result = subprocess.run(
            cmd, input=document, capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=timeout,
            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},  # SwiftBar may run us under a C locale
        )
    except FileNotFoundError:
        raise LlmDisabled(f"llm not found at {cfg['llm_path']}")
    except OSError as exc:
        raise LlmDisabled(f"cannot run {cfg['llm_path']}: {exc}")
    if result.returncode == 0:
        return result.stdout
    stderr = ' '.join((result.stderr or '').split())
    tail = llm_error_text(stderr)
    if re.search(r'unknown model', stderr, re.I):
        raise LlmDisabled(f"model '{cfg['summary_model']}' is unknown to {cfg['llm_path']} "
                          f"(plugin not installed?): {tail}")
    # Account-level trouble: no key, bad key, or an OpenRouter balance that cannot cover the
    # call (402, or the in-flight budget it derives from the balance). No story can succeed.
    if re.search(r'no key found|api.?key|unauthori[sz]ed|authentication|\bcredits?\b'
                 r'|rate.?limit|in-flight|\b40[123]\b|\b429\b', stderr, re.I):
        raise LlmDisabled(f"llm could not reach {cfg['summary_model']}: {tail}")
    if options and re.search('|'.join(re.escape(key) for key in options)
                             + r'|not a valid option|extra inputs are not permitted', stderr, re.I):
        raise LlmOptionRejected(tail)
    raise RuntimeError(f"llm exit {result.returncode}: {tail}")


async def summarize_with_llm(session, hits, cfg: dict, summaries: dict) -> set:
    """Fill `summaries` from the llm path for the hits given; returns the ids that were tried and
    failed. At most HN_LLM_CONCURRENCY calls run at once and the whole batch stops at
    HN_LLM_SECTION_BUDGET seconds. Per-story failures leave a miss marker; a run-level failure
    (binary, model, auth) logs one line and disables the path for the rest of the run.
    """
    failed = set()
    state = {'disabled': False, 'consecutive_timeouts': 0, 'options': cfg['summary_options']}
    deadline = time.monotonic() + HN_LLM_SECTION_BUDGET
    semaphore = asyncio.Semaphore(HN_LLM_CONCURRENCY)

    async def one(hit):
        story_id = hit.get('objectID')
        async with semaphore:
            if state['disabled'] or state['consecutive_timeouts'] >= HN_LLM_MAX_CONSECUTIVE_TIMEOUTS:
                return
            item = await fetch_hn_thread(session, story_id)
            if not item:
                debug(f"{story_id}: no Algolia thread")
                return  # free to retry next run
            document = flatten_hn_thread(item, cfg['thread_char_budget'])
            remaining = deadline - time.monotonic()
            if remaining < 3:
                return  # section budget spent; not this story's fault
            timeout = min(cfg['llm_timeout'], remaining)
            try:
                while True:
                    try:
                        raw = await asyncio.to_thread(
                            run_llm, document, SUMMARY_INSTRUCTION, cfg, timeout, state['options'])
                        break
                    except LlmOptionRejected as exc:
                        warn(f"{cfg['summary_model']} rejected summary_options "
                             f"{state['options']}; retrying without them: {exc}")
                        state['options'] = {}
            except LlmDisabled as exc:
                if not state['disabled']:
                    state['disabled'] = True
                    warn(f"llm summaries off for this run: {exc}")
                return
            except subprocess.TimeoutExpired:
                state['consecutive_timeouts'] += 1
                debug(f"{story_id}: llm timed out after {timeout:.0f}s")
                save_miss_marker(story_id)
                failed.add(story_id)
                return
            except Exception as exc:
                debug(f"{story_id}: {exc}")
                save_miss_marker(story_id)
                failed.add(story_id)
                return
            state['consecutive_timeouts'] = 0
            summary = sanitize_llm_summary(raw)
            if summary:
                summaries[story_id] = summary
                save_summary_cache(story_id, summary)
            else:
                debug(f"{story_id}: empty summary from {cfg['summary_model']}")
                save_miss_marker(story_id)
                failed.add(story_id)

    try:
        await asyncio.wait_for(asyncio.gather(*(one(hit) for hit in hits)),
                               timeout=HN_LLM_SECTION_BUDGET + 5)
    except asyncio.TimeoutError:
        warn("Hacker News llm budget exhausted; remaining stories show comment counts")
    return failed


async def summarize_cli(story_id: str, model: Optional[str], options: Optional[dict]) -> int:
    """`--summarize`: run one story through the llm path, bypassing every cache, and report."""
    cfg = dict(summary_config())
    if model:
        cfg['summary_model'] = model
    if options is not None:
        cfg['summary_options'] = options
    timeout = ClientTimeout(total=REQUEST_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout, connector=aiohttp.TCPConnector(ssl=False)) as session:
        item = await fetch_hn_thread(session, story_id)
    if not item:
        print(f"no Algolia item for story {story_id}", file=sys.stderr)
        return 1
    document = flatten_hn_thread(item, cfg['thread_char_budget'])
    kept = document.split('\nComments:\n', 1)[1].count('\n')
    print(f"story:   {story_id} — {item.get('title')}")
    print(f"thread:  {len(document):,} chars; {kept} of {count_comments(item)} comments kept")
    print(f"model:   {cfg['summary_model']}  options: {json.dumps(cfg['summary_options'])}")
    started = time.monotonic()
    try:
        try:
            raw = run_llm(document, SUMMARY_INSTRUCTION, cfg, cfg['llm_timeout'] * 3, cfg['summary_options'])
        except LlmOptionRejected as exc:
            print(f"options rejected, retrying without: {exc}")
            raw = run_llm(document, SUMMARY_INSTRUCTION, cfg, cfg['llm_timeout'] * 3, {})
    except Exception as exc:
        print(f"failed after {time.monotonic() - started:.1f}s: {exc}", file=sys.stderr)
        return 1
    elapsed = time.monotonic() - started
    summary = sanitize_llm_summary(raw)
    tooltip = format_hn_tooltip(summary) if summary else ''
    over = "  (over llm_timeout)" if elapsed > cfg['llm_timeout'] else ""
    print(f"elapsed: {elapsed:.1f}s{over}")
    print(f"tooltip: {estimate_tooltip_lines(tooltip)} lines of {HN_TOOLTIP_MAX_LINES}, "
          f"{len(summary)} chars")
    print("---")
    print(summary or "(nothing usable in the reply)")
    if DEBUG:
        print("--- raw ---")
        print(raw)
    return 0 if summary else 1


# Constants
TECHMEME_URL = "https://www.techmeme.com/"
HN_URL = "https://news.ycombinator.com/"
LOBSTERS_URL = "https://lobste.rs"
HNCOMPANION_API = "https://app.hncompanion.com/api/posts/"
STLTODAY_URL = "https://www.stltoday.com"
BND_URL = "https://www.bnd.com"
STLPR_URL = "https://www.stlpr.org"
SIMONWILLISON_FEED = "https://simonwillison.net/atom/everything/"
ALGOLIA_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
REQUEST_TIMEOUT = 10
MAX_HEADLINES = 15
MAX_TOPIC_HEADLINES = 8  # Smaller cap for the topic-interest sections
TRIM_LENGTH = 100  # Character limit for headlines

# STLToday configuration
STL_EXCLUDED_CATEGORIES = {
    "LatestVideo",
    "Partner",
    "Curated Commerce",
    "Print Ads",
    "Listen NowPodcasts",
    "InteractWith Us",
    "Local Businesses",
    "Nation & World",
    "Winning STL",
}

STL_CATEGORY_ABBREVIATIONS = {
    'Opinion': 'OpEd',
    'Business': 'Biz',
    'Life & Entertainment': 'Life',
    'RecommendedFor You': 'Picks',
    'Uncategorized': 'Top',
    'TheLatest': 'New',
}

# BND configuration
BND_CATEGORY_ABBREVIATIONS = {
    'Opinion Columns & Blogs': '[Op Ed]',
    'High School Football': '[HS Football]',
    'Crime': '[Crime]',
    'Metro-East News': '[Metro]',
    'Business': '[Biz]',
    'Food & Drink': '[Food]',
    'St. Louis Cardinals': '[Cards]',
    'Belleville': '[BLV]',
    'Latest News': '[Latest]'
}

@dataclass
class Article:
    headline: str
    link: str
    summary: str = ''
    category: str = ''

    def with_full_link(self, base_url: str) -> 'Article':
        if not self.link.startswith('http'):
            self.link = f"{base_url}{self.link}"
        return self


async def getDOM(url):
    timeout = ClientTimeout(total=REQUEST_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout, connector=aiohttp.TCPConnector(ssl=False)) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                return BeautifulSoup(await response.text(), 'html.parser')
    except Exception as e:
        return f"--⚠️ Error fetching {url}: {e} | color=red\n"


def setup_sync_session() -> requests.Session:
    """Setup requests session with retry logic and headers"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Cache-Control': 'max-age=0',
        'Referer': 'https://www.google.com/',
        'Origin': 'https://www.bnd.com',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'cross-site'
    }
    session = requests.Session()
    session.headers.update(headers)

    retry_strategy = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount('https://', adapter)
    session.mount('http://', adapter)

    return session


async def fetch_and_buffer(scraper):
    buffer = StringIO()
    await scraper(buffer)
    return buffer.getvalue()


def format_headline(title, url, tags=None, summary=None):
    """Format headlines with full title and summary tooltip."""
    tags_text = f"[{', '.join(tags)}] " if tags else ""
    full_title = f"{tags_text}{title}"

    # Use summary as tooltip if available, otherwise use title
    tooltip_text = summary if summary else title

    # Escape special characters for SwiftBar:
    # 1. Escape backslashes first (must be first to avoid double-escaping)
    tooltip_text = tooltip_text.replace('\\', '\\\\')
    # 2. Escape double quotes
    tooltip_text = tooltip_text.replace('"', '\\"')
    # 3. Escape pipe characters (they have special meaning in SwiftBar)
    tooltip_text = tooltip_text.replace('|', '\\|')
    # 4. Escape newlines (raw newlines break SwiftBar's line-based parsing)
    tooltip_text = tooltip_text.replace('\n', '\\n')

    # Escape title too for any pipes or special chars
    full_title = full_title.replace('|', ' ')  # Remove pipes from display title
    full_title = full_title.replace('"', '\\"')

    return f"-- {full_title} | href={url} tooltip=\"{tooltip_text}\" trim=false\n"


def format_stl_headline(article: Article) -> str:
    """Format STLToday article for SwiftBar menu display"""
    display_headline = f"[{article.category}] {article.headline}"

    # Escape quotes in tooltip
    tooltip_text = article.summary.replace('\\', '\\\\').replace('"', '\\"')
    return f"-- {display_headline} | href={article.link} tooltip=\"{tooltip_text}\"\n"


def format_bnd_headline(article: Article) -> str:
    """Format BND article for SwiftBar menu display"""
    display_headline = article.headline
    if article.category:
        abbreviated = BND_CATEGORY_ABBREVIATIONS.get(article.category, f'[{article.category}]')
        display_headline = f"{abbreviated} {article.headline}"
        tooltip_text = f"{article.category}: {article.headline}"
    else:
        tooltip_text = article.headline

    # Add summary to tooltip if available
    if article.summary:
        tooltip_text = f"{tooltip_text}\n\n{article.summary}"

    # Escape quotes in tooltip
    tooltip_text = tooltip_text.replace('\\', '\\\\').replace('"', '\\"')
    return (f'-- '
            f'{display_headline} | href={article.link} tooltip="{tooltip_text}"\n')


def format_stlpr_headline(article: Article) -> str:
    """Format STL PR article for SwiftBar menu display"""
    # Map long category names to concise one-word versions
    category_map = {
        "Government, Politics & Issues": "Politics",
        "News Briefs": "News",
        "Economy & Business": "Business",
        "Race, Identity & Faith": "Society",
        "Culture & History": "Culture",
        "Health, Science & Environment": "Science",
        "Sports": "Sports",
        "Arts": "Arts",
    }

    # Use mapped category if available, otherwise use original
    short_category = category_map.get(article.category, article.category)
    display_headline = f"[{short_category}] {article.headline}"

    # Use subtitle/summary as tooltip if available, otherwise use headline
    tooltip_text = article.summary if article.summary else article.headline

    # Escape quotes in tooltip
    tooltip_text = tooltip_text.replace('\\', '\\\\').replace('"', '\\"')
    return f"-- {display_headline} | href={article.link} tooltip=\"{tooltip_text}\"\n"


async def fetch_hn_topic(session, queries, max_items=MAX_TOPIC_HEADLINES, since_epoch=None):
    """Search HN story titles via Algolia for exact-phrase queries; dedupe, newest first.

    since_epoch keeps a quiet topic honest: without it these searches happily return the same
    hits for months, because a niche phrase has no fresher stories to offer.
    """
    numeric_filters = ['points>5']
    if since_epoch:
        numeric_filters.append(f'created_at_i>{int(since_epoch)}')
    hits_by_id = {}
    for query in queries:
        try:
            params = {
                'query': f'"{query}"',
                'tags': 'story',
                'restrictSearchableAttributes': 'title',
                'advancedSyntax': 'true',
                'numericFilters': ','.join(numeric_filters),
                'hitsPerPage': str(max_items),
            }
            async with session.get(ALGOLIA_SEARCH_URL, params=params) as response:
                response.raise_for_status()
                data = await response.json()
            for hit in data.get('hits', []):
                hits_by_id.setdefault(hit.get('objectID'), hit)
        except Exception:
            continue
    # Dedupe reposts: the same title submitted as separate stories — keep the highest-scoring
    hits_by_title = {}
    for hit in hits_by_id.values():
        key = re.sub(r'\W+', ' ', hit.get('title', '').lower()).strip()
        best = hits_by_title.get(key)
        if best is None or (hit.get('points', 0) or 0) > (best.get('points', 0) or 0):
            hits_by_title[key] = hit
    hits = sorted(hits_by_title.values(), key=lambda h: h.get('created_at_i', 0), reverse=True)
    return hits[:max_items]


async def resolve_hn_summaries(session, hits) -> dict:
    """Map story id -> HN Companion discussion summary, local cache first, misses left out."""
    summaries = {}
    uncached_ids = []
    for hit in hits:
        story_id = hit.get('objectID')
        cached = get_cached_summary(story_id)
        if cached:
            summaries[story_id] = cached
        else:
            uncached_ids.append(story_id)

    if uncached_ids:
        companion_results = await asyncio.gather(
            *(fetch_hncompanion_summary(session, sid) for sid in uncached_ids)
        )
        for sid, condensed in zip(uncached_ids, companion_results):
            if condensed:
                summaries[sid] = condensed
                save_summary_cache(sid, condensed)

    return summaries


async def fetch_techmeme(buffer=None):
    if buffer is None:
        buffer = StringIO()
    result = await getDOM(TECHMEME_URL)
    if isinstance(result, str):
        buffer.write(result)
        return

    stories = result.select('.clus')
    buffer.write(f"Techmeme | href={TECHMEME_URL} color=#00C853\n")
    for story in stories[:MAX_HEADLINES]:
        try:
            story_link = story.select_one('.ourh')['href']
            story_title = story.select_one('.ourh').text

            # Extract the full excerpt that appears after the </strong> tag
            summary = ''
            ii_elem = story.select_one('.ii')
            if ii_elem:
                # Get the HTML string to find text after </strong> 
                ii_html = str(ii_elem)
                if '</strong>' in ii_html:
                    # Get everything after the closing </strong> tag
                    after_strong = ii_html.split('</strong>', 1)[1]
                    # Parse it to extract just the text
                    temp_soup = BeautifulSoup(after_strong, 'html.parser')
                    excerpt_text = temp_soup.get_text(separator=' ', strip=True)
                    # Clean up leading separators (nbsp, em dash, spaces, etc.)
                    excerpt_text = re.sub(r'^[\s\xa0—–\-]+', '', excerpt_text).strip()
                    if excerpt_text:
                        summary = excerpt_text

            buffer.write(format_headline(story_title, story_link, summary=summary))
        except Exception:
            continue


async def fetch_hnt(buffer=None):
    if buffer is None:
        buffer = StringIO()
    # Use Algolia API for richer metadata in a single request
    algolia_url = "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=15"
    buffer.write(f"Hacker News | href={HN_URL} color=#FF6600\n")

    try:
        timeout = ClientTimeout(total=REQUEST_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout, connector=aiohttp.TCPConnector(ssl=False)) as session:
            async with session.get(algolia_url) as response:
                response.raise_for_status()
                data = await response.json()

            hits = data.get("hits", [])[:MAX_HEADLINES]

            # Layered summary lookup: local cache -> HN Companion API -> llm (front page only)
            summaries = await resolve_hn_summaries(session, hits)
            failed = set()
            cfg = summary_config()
            if cfg['summaries']:
                wanted = [
                    hit for hit in hits
                    if hit.get('objectID') not in summaries
                    and (hit.get('num_comments') or 0) >= HN_LLM_MIN_COMMENTS
                    and not has_fresh_miss_marker(hit.get('objectID'))
                ]
                if wanted:
                    failed = await summarize_with_llm(session, wanted, cfg, summaries)

            for hit in hits:
                title = hit.get("title", "Untitled")
                story_id = hit.get("objectID")
                points = hit.get("points", 0)
                num_comments = hit.get("num_comments", 0)

                # Format title with upvotes and comments
                formatted_title = f"[{points}↑] {title} ({num_comments}􀌪)"

                if story_id in summaries:
                    summary = summaries[story_id]
                elif story_id in failed:
                    summary = "See HN discussion"  # the llm path ran for this story and failed
                else:
                    summary = f"{num_comments} comments"

                # Format summary with visual structure, then escape special characters
                formatted_summary = format_hn_tooltip(summary)
                tooltip_text = (
                    formatted_summary
                    .replace("\\", "\\\\")  # Escape backslashes first
                    .replace("\n", "\\n")   # Convert newlines to literal \n for SwiftBar
                    .replace('"', '\\"')    # Escape quotes
                    .replace("|", "\\|")    # Escape pipes
                )
                formatted_title_escaped = formatted_title.replace("|", " ").replace(
                    '"', '\\"'
                )

                buffer.write(
                    f'-- {formatted_title_escaped} | href={HN_URL}item?id={story_id} tooltip="{tooltip_text}" trim=false\n'
                )
    except Exception as e:
        buffer.write(f"-- Error fetching HN: {e} | color=red\n")

async def fetch_lobsters(buffer=None):
    if buffer is None:
        buffer = StringIO()
    result = await getDOM(LOBSTERS_URL)
    if isinstance(result, str):
        buffer.write(result)
        return

    stories = result.select("ol.stories > li")[:MAX_HEADLINES]
    buffer.write(f"Lobste.rs | href={LOBSTERS_URL} color=#CC2200\n")

    items = []
    for story in stories:
        try:
            title_elem = story.select_one(".link > a.u-url")
            if not title_elem:
                continue
            title = title_elem.text
            url = title_elem['href']
            if not url.startswith('http'):
                url = f"{LOBSTERS_URL}{url}"  # Ask-style posts link back into lobste.rs
            tags = [tag.text for tag in story.select(".tags > a")]

            # Try to extract description if available
            summary = ''
            desc_elem = story.select_one(".description")
            if desc_elem:
                summary = clean_summary(desc_elem.text)

            items.append((title, url, tags, summary))
        except Exception:
            continue

    # .description is the submitter's own note and is empty for nearly every link post,
    # so read the linked article's own blurb instead
    previews = await fetch_previews(url for _, url, _, summary in items if not summary)
    for title, url, tags, summary in items:
        buffer.write(format_headline(title, url, tags, summary or previews.get(url)))


async def fetch_stltoday(buffer=None):
    if buffer is None:
        buffer = StringIO()

    buffer.write(f"STLToday | href={STLTODAY_URL} color=#1E88E5\n")

    def sync_fetch_stl():
        try:
            session = setup_sync_session()
            response = session.get(STLTODAY_URL, timeout=20)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            articles = []

            blocks = soup.select('section.block')
            for block in blocks:
                category_elem = block.select_one('div.block-title-inner h3')
                category = category_elem.get_text(strip=True) if category_elem else 'Uncategorized'

                if category in STL_EXCLUDED_CATEGORIES:
                    continue

                # Apply category abbreviation if available
                category = STL_CATEGORY_ABBREVIATIONS.get(category, category)

                card_grid = block.select('article')
                for article_elem in card_grid:
                    try:
                        title_elem = article_elem.select_one('.card-headline a, .tnt-headline a, .tnt-asset-link')
                        if not title_elem:
                            continue

                        headline = title_elem.get('aria-label') if title_elem and title_elem.has_attr('aria-label') else title_elem.get_text(strip=True)
                        link = title_elem['href'] if title_elem else ''
                        summary_elem = article_elem.select_one('div.card-lead p')
                        summary = summary_elem.get_text(strip=True) if summary_elem else "No summary"

                        article = Article(
                            headline=headline,
                            link=link,
                            summary=summary,
                            category=category
                        ).with_full_link(STLTODAY_URL)

                        articles.append(article)

                        if len(articles) >= MAX_HEADLINES:
                            break

                    except Exception:
                        continue

                if len(articles) >= MAX_HEADLINES:
                    break

            return articles[:MAX_HEADLINES]

        except Exception as e:
            return [f"Error fetching STLToday: {e}"]

    try:
        articles = await asyncio.to_thread(sync_fetch_stl)

        if isinstance(articles, list) and articles and isinstance(articles[0], str):
            # Error message
            buffer.write(f"--⚠️ {articles[0]}\n")
        else:
            missing = [a.link for a in articles if a.summary in ('', 'No summary')]
            previews = await fetch_previews(missing)
            for article in articles:
                if article.summary in ('', 'No summary'):
                    article.summary = previews.get(article.link, '')
                buffer.write(format_stl_headline(article))

    except Exception as e:
        buffer.write(f"--⚠️ Error fetching STLToday: {e} | color=red\n")


async def fetch_bnd(buffer=None):
    if buffer is None:
        buffer = StringIO()

    buffer.write(f"BND | href={BND_URL} color=#1976D2\n")

    def sync_fetch_bnd():
        try:
            session = setup_sync_session()
            time.sleep(1)  # Be nice to the server
            response = session.get(BND_URL, timeout=20, verify=True)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')
            articles = []
            seen_links = set()

            def normalize_link(link: str) -> str:
                """Normalize link by removing fragments and ensuring full URL"""
                base_link = link.split('#')[0]
                if not base_link.startswith('http'):
                    base_link = f"{BND_URL}{base_link}"
                return base_link

            def clean_text(text: str) -> str:
                """Clean whitespace and newlines from text"""
                return re.sub(r'\s+', ' ', text).strip()

            def extract_article_from_element(element: Tag, category: str = '') -> Optional[Article]:
                """Extract article information from HTML element"""
                headline_elem = element.find('h3')
                if not headline_elem or not (link_elem := headline_elem.find('a')):
                    return None

                link = link_elem.get('href', '')
                normalized_link = normalize_link(link)

                if not link or normalized_link in seen_links:
                    return None

                seen_links.add(normalized_link)

                if not category:
                    kicker = element.find(class_='kicker')
                    category = clean_text(kicker.text) if kicker else ''

                # Extract summary/description if available
                summary = ''
                summary_elem = element.find('p', class_='blurb')
                if not summary_elem:
                    summary_elem = element.find('div', class_='summary')
                if not summary_elem:
                    summary_elem = element.find('p')
                if summary_elem:
                    summary = clean_text(summary_elem.text)

                return Article(
                    headline=clean_text(headline_elem.text),
                    link=link,
                    summary=summary,
                    category=category
                ).with_full_link(BND_URL)

            # Get main grid articles
            if content_area := soup.find('section', class_='grid'):
                for article_elem in content_area.find_all('article', recursive=True):
                    if not article_elem.find_parent(class_='partner-digest-group'):
                        if article := extract_article_from_element(article_elem):
                            articles.append(article)
                            if len(articles) >= MAX_HEADLINES:
                                break

            # Get latest news articles if we need more
            if len(articles) < MAX_HEADLINES:
                if latest_section := soup.find('div', attrs={'data-tb-region': 'latest'}):
                    for article_elem in latest_section.find_all('div', class_='package'):
                        if article := extract_article_from_element(article_elem, category='Latest News'):
                            articles.append(article)
                            if len(articles) >= MAX_HEADLINES:
                                break

            return articles[:MAX_HEADLINES]

        except Exception as e:
            return [f"Error fetching BND: {e}"]

    try:
        articles = await asyncio.to_thread(sync_fetch_bnd)

        if isinstance(articles, list) and articles and isinstance(articles[0], str):
            # Error message
            buffer.write(f"--⚠️ {articles[0]}\n")
        else:
            for article in articles:
                buffer.write(format_bnd_headline(article))

    except Exception as e:
        buffer.write(f"--⚠️ Error fetching BND: {e} | color=red\n")


async def fetch_stlpr(buffer=None):
    if buffer is None:
        buffer = StringIO()

    buffer.write(f"STL PR | href={STLPR_URL} color=#0D47A1\n")

    def sync_fetch_stlpr():
        try:
            session = setup_sync_session()
            response = session.get(STLPR_URL, timeout=20)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            articles = []

            # Find all ps-promo elements (custom web component)
            promos = soup.find_all('ps-promo')

            for promo in promos:
                try:
                    # Get all links in the promo
                    links = promo.find_all('a', href=True)
                    if len(links) < 3:
                        continue

                    # Link structure:
                    # Link #0: Article link (empty text, has aria-label)
                    # Link #1: Category link (has category name as text)
                    # Link #2: Headline link (has the full headline as text)
                    category = links[1].get_text(strip=True)
                    headline = links[2].get_text(strip=True)
                    link = links[2].get('href', '')

                    if not link.startswith('http'):
                        link = f"{STLPR_URL}{link}"

                    # Get description from stripped strings
                    # Format: [Author, '/', Publication, Category, Headline, Author (again), Description]
                    # The description is the last item in the list (if it exists and is not metadata)
                    all_text = list(promo.stripped_strings)
                    summary = ''
                    if len(all_text) > 0:
                        # The description is typically the last item
                        last_item = all_text[-1]
                        # Filter out non-description items:
                        # - Category, headline
                        # - Publication names
                        # - Time durations (e.g., "4:12", "40:16")
                        # - Single-character items or forward slash
                        # - Items that appear to be author names (in one of the link texts)
                        link_texts = [link.get_text(strip=True) for link in links]
                        is_link_text = last_item in link_texts
                        is_duration = ':' in last_item and len(last_item) < 10  # e.g., "4:12"
                        is_metadata = last_item in ['/', 'St. Louis Public Radio', 'Belleville News-Democrat', 'Nebraska Public Media']

                        if (last_item != category and
                            last_item != headline and
                            not is_link_text and
                            not is_duration and
                            not is_metadata and
                            len(last_item) > 10):  # Descriptions are usually longer than 10 chars
                            summary = last_item

                    article = Article(
                        headline=headline,
                        link=link,
                        summary=summary,
                        category=category if category else 'Uncategorized'
                    )

                    articles.append(article)

                    if len(articles) >= MAX_HEADLINES:
                        break

                except Exception:
                    continue

            return articles[:MAX_HEADLINES]

        except Exception as e:
            return [f"Error fetching STL PR: {e}"]

    try:
        articles = await asyncio.to_thread(sync_fetch_stlpr)

        if isinstance(articles, list) and articles and isinstance(articles[0], str):
            # Error message
            buffer.write(f"--⚠️ {articles[0]}\n")
        else:
            previews = await fetch_previews(a.link for a in articles if not a.summary)
            for article in articles:
                article.summary = article.summary or previews.get(article.link, '')
                buffer.write(format_stlpr_headline(article))

    except Exception as e:
        buffer.write(f"--⚠️ Error fetching STL PR: {e} | color=red\n")


async def fetch_simonwillison(buffer=None):
    if buffer is None:
        buffer = StringIO()

    buffer.write(f"Simon Willison | href=https://simonwillison.net/ color=#F5A623\n")

    def sync_fetch():
        feed = feedparser.parse(SIMONWILLISON_FEED)
        entries = []
        for entry in feed.entries[:MAX_HEADLINES]:
            title = entry.get("title", "Untitled")
            link = entry.get("link", "").split("#")[0]
            tags = [t.get("term", "") for t in entry.get("tags", [])]
            summary_html = entry.get("summary", "")
            summary_text = BeautifulSoup(summary_html, "html.parser").get_text(" ", strip=True)
            entries.append((title, link, tags, summary_text))
        return entries

    try:
        entries = await asyncio.to_thread(sync_fetch)
        for title, link, tags, summary_text in entries:
            display_tags = tags[:2]
            full_tags = ", ".join(tags) if tags else ""
            tooltip = f"Tags: {full_tags} -- {summary_text}" if full_tags else summary_text
            buffer.write(format_headline(title, link, tags=display_tags, summary=tooltip))
    except Exception as e:
        buffer.write(f"--⚠️ Error fetching Simon Willison: {e} | color=red\n")


@dataclass
class Topic:
    """A themed section: a handful of feeds, optionally supplemented by fresh Hacker News hits.

    max_age_days is what keeps a section honest — a feed that stops publishing (Stronger By
    Science went quiet for months) drops out instead of filling the section with last spring.
    """
    name: str
    home_url: str
    color: str
    feeds: tuple
    hn_queries: tuple = ()
    max_items: int = MAX_TOPIC_HEADLINES
    max_age_days: int = 21
    per_feed_max: int = 3
    exclude: Optional[str] = None


@dataclass
class TopicItem:
    when: float          # epoch seconds, for the newest-first merge
    title: str
    link: str            # what the row opens
    tag: str
    summary: str = ''
    preview_url: str = ''  # article behind an HN discussion link, for the tooltip


TOPICS = (
    Topic(
        name="Local & Agentic AI",
        home_url="https://www.latent.space",
        color="#7C4DFF",
        feeds=(
            ("https://www.latent.space/feed", "Latent Space"),
            ("https://openai.com/news/rss.xml", "OpenAI"),
            ("https://deepmind.google/blog/rss.xml", "DeepMind"),
            ("https://arstechnica.com/ai/feed/", "Ars"),
            ("https://huggingface.co/blog/feed.xml", "HF"),
            ("https://lobste.rs/t/ai.rss", "lobsters"),
            ("https://github.com/ml-explore/mlx/releases.atom", "mlx"),
            ("https://github.com/ml-explore/mlx-lm/releases.atom", "mlx-lm"),
        ),
        hn_queries=("mlx",),
        max_items=12,
        max_age_days=30,
        per_feed_max=2,
    ),
    Topic(
        name="Home Lab",
        home_url="https://www.servethehome.com",
        color="#607D8B",
        feeds=(
            ("https://www.servethehome.com/feed/", "STH"),
            ("https://selfh.st/rss/", "selfh.st"),
            ("https://www.jeffgeerling.com/blog.xml", "Geerling"),
        ),
        hn_queries=("homelab", "self-hosted"),
        max_age_days=30,
    ),
    Topic(
        name="NBA",
        home_url="https://www.espn.com/nba/",
        color="#C9082A",
        feeds=(
            ("https://www.espn.com/espn/rss/nba/news", "ESPN"),
            ("https://basketball.realgm.com/rss/wiretap/0/0.xml", "RealGM"),
        ),
        max_age_days=7,
        per_feed_max=4,
        exclude=r"Get Your Latest NBA News",
    ),
    Topic(
        name="EV / Solar",
        home_url="https://electrek.co",
        color="#2E7D32",
        feeds=(
            ("https://electrek.co/feed/", "Electrek"),
            ("https://www.pv-magazine.com/feed/", "PV"),
            ("https://www.canarymedia.com/rss", "Canary"),
        ),
        max_age_days=7,
    ),
    Topic(
        name="Fitness 50+",
        home_url="https://peterattiamd.com",
        color="#E91E63",
        feeds=(
            ("https://peterattiamd.com/feed/", "Attia"),
            ("https://www.physiologicallyspeaking.com/feed", "Physiology"),
            ("https://www.outsideonline.com/health/training-performance/feed/", "Outside"),
            ("https://www.barbellmedicine.com/feed/", "Barbell Med"),
            ("https://www.strongerbyscience.com/feed/", "SBS"),
        ),
        max_age_days=45,
    ),
)


def dedupe_topic_items(items, max_items):
    """Newest first, one row per story — the same piece often lands in two feeds and on HN."""
    seen_titles, seen_links, deduped = set(), set(), []
    for item in sorted(items, key=lambda entry: entry.when, reverse=True):
        title_key = re.sub(r'\W+', ' ', item.title.lower()).strip()
        link_key = (item.preview_url or item.link).split('?')[0].rstrip('/')
        if title_key in seen_titles or (link_key and link_key in seen_links):
            continue
        seen_titles.add(title_key)
        seen_links.add(link_key)
        deduped.append(item)
        if len(deduped) >= max_items:
            break
    return deduped


async def fetch_topic_hn_items(topic, cutoff):
    """Hacker News hits for a topic, newer than cutoff, with discussion summaries where cached.

    No llm/Gemini fallback here — niche stories are rarely worth a paid call; a story without a
    cached discussion summary falls back to the linked article's own blurb in render_topic.
    """
    try:
        timeout = ClientTimeout(total=REQUEST_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout, connector=aiohttp.TCPConnector(ssl=False)) as session:
            hits = await fetch_hn_topic(session, topic.hn_queries,
                                        max_items=topic.per_feed_max, since_epoch=cutoff)
            summaries = await resolve_hn_summaries(session, hits)
    except Exception:
        return []  # The feeds carry the section on their own

    items = []
    for hit in hits:
        story_id = hit.get('objectID')
        summary = summaries.get(story_id)
        items.append(TopicItem(
            when=hit.get('created_at_i', 0) or 0,
            title=hit.get('title', 'Untitled'),
            link=f"{HN_URL}item?id={story_id}",
            tag=f"{hit.get('points', 0) or 0}↑ HN",
            summary=format_hn_tooltip(summary) if summary else '',
            preview_url=hit.get('url') or '',
        ))
    return items


async def render_topic(topic: Topic, buffer=None):
    """Render one topic section: its feeds merged newest-first inside the recency window."""
    if buffer is None:
        buffer = StringIO()

    buffer.write(f"{topic.name} | href={topic.home_url} color={topic.color}\n")
    cutoff = time.time() - topic.max_age_days * 86400

    parsed = await asyncio.gather(
        *(asyncio.to_thread(feedparser.parse, feed_url) for feed_url, _ in topic.feeds),
        return_exceptions=True,
    )

    items = []
    for (_, tag), feed in zip(topic.feeds, parsed):
        if isinstance(feed, BaseException):
            continue  # A failing feed is skipped, not fatal
        kept = 0
        for entry in getattr(feed, 'entries', []):
            if kept >= topic.per_feed_max:
                break
            title = entry.get('title', 'Untitled')
            if topic.exclude and re.search(topic.exclude, title):
                continue
            published = entry.get('published_parsed') or entry.get('updated_parsed')
            when = calendar.timegm(published) if published else 0
            if when < cutoff:
                continue
            summary_html = entry.get('summary', '')
            items.append(TopicItem(
                when=when,
                title=title,
                link=entry.get('link', ''),
                tag=tag,
                summary=clean_summary(BeautifulSoup(summary_html, 'html.parser').get_text(' ', strip=True)),
            ))
            kept += 1

    if topic.hn_queries:
        items.extend(await fetch_topic_hn_items(topic, cutoff))

    items = dedupe_topic_items(items, topic.max_items)
    if not items:
        buffer.write(f"--No stories in the last {topic.max_age_days} days | color=gray\n")
        return

    previews = await fetch_previews(
        item.preview_url or item.link
        for item in items if len(item.summary) < PREVIEW_SHORT_SUMMARY
    )
    for item in items:
        preview = previews.get(item.preview_url or item.link, '')
        summary = max((item.summary, preview), key=len)
        buffer.write(format_headline(item.title, item.link, tags=[item.tag], summary=summary or None))


async def main():
    # Menubar Symbol
    print("􀤦")
    print("---")

    start = time.time()
    prune_cache(PREVIEW_CACHE_DIR, PREVIEW_PRUNE_DAYS)
    prune_cache(CACHE_DIR, PREVIEW_PRUNE_DAYS)

    # Section order matters: a submenu opens level with its parent row, and macOS clips
    # tooltips that extend above the screen. Hacker News carries the tallest tooltips,
    # so it sits 6th, giving its stories enough headroom for the full summary.
    sections = await asyncio.gather(
        fetch_and_buffer(fetch_stltoday),
        fetch_and_buffer(fetch_stlpr),
        fetch_and_buffer(fetch_bnd),
        fetch_and_buffer(fetch_techmeme),
        fetch_and_buffer(fetch_lobsters),
        fetch_and_buffer(fetch_hnt),
        fetch_and_buffer(fetch_simonwillison),
        *(fetch_and_buffer(functools.partial(render_topic, topic)) for topic in TOPICS),
    )

    # Print each section sequentially
    for section in sections:
        print(section)

    end = time.time()
    print("---")
    print(f"Updated at {datetime.datetime.now().strftime('%I:%M %p')} (fetched in {round(end - start, 2)}s)")
    print("Refresh | refresh=true")


def parse_cli(argv):
    """SwiftBar runs the plugin with no arguments. `--summarize <id> [--model M] [-o k=v ...]`
    is the bake-off hook: one story through the llm path, no caches touched."""
    import argparse
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--summarize', metavar='STORY_ID')
    parser.add_argument('--model')
    parser.add_argument('-o', '--option', action='append', metavar='KEY=VALUE',
                        help='replace summary_options; "-o none" sends no options')
    args, _ = parser.parse_known_args(argv)
    options = None
    if args.option:
        options = {} if args.option == ['none'] else dict(opt.split('=', 1) for opt in args.option)
    return args, options


if __name__ == "__main__":
    cli, cli_options = parse_cli(sys.argv[1:])
    if cli.summarize:
        sys.exit(asyncio.run(summarize_cli(cli.summarize, cli.model, cli_options)))
    asyncio.run(main())
