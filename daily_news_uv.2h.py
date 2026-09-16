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
# <swiftbar.version>v2.1</swiftbar.version>
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
import subprocess
from urllib.parse import urlsplit

# Cache directory for HN comment summaries
CACHE_DIR = os.path.expanduser("~/.cache/swiftbar_hn_summaries")
os.makedirs(CACHE_DIR, exist_ok=True)


def get_cached_summary(story_id: str) -> Optional[str]:
    """Retrieve cached summary for a HN story."""
    cache_file = os.path.join(CACHE_DIR, f"{story_id}.json")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r') as f:
                data = json.load(f)
                return data.get('summary')
        except Exception:
            return None
    return None


def save_summary_cache(story_id: str, summary: str) -> None:
    """Save summary to cache."""
    cache_file = os.path.join(CACHE_DIR, f"{story_id}.json")
    try:
        with open(cache_file, 'w') as f:
            json.dump({
                'story_id': story_id,
                'summary': summary,
                'timestamp': datetime.datetime.now().isoformat()
            }, f)
    except Exception as e:
        pass  # Silently fail on cache write


def get_hn_discussion_summary(story_id: str) -> str:
    """Fetch HN discussion summary using LLM with caching."""
    # Check cache first
    cached = get_cached_summary(story_id)
    if cached:
        return cached

    try:
        # Run llm command with HN plugin (use absolute path for SwiftBar compatibility)
        cmd = [
            "/Users/hodgesd/.local/bin/llm",
            "-m",
            "gemini-2.5-flash",
            "-f",
            f"hn:{story_id}",
            "summarize this discussion. 2 structured paragraphs max. focus on key insights and disagreements.",
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,  # Gemini 2.5 Flash: 1-3s typical, 15s allows generous buffer
        )

        if result.returncode == 0:
            summary = result.stdout.strip()
            # Cache the result
            save_summary_cache(story_id, summary)
            return summary
        else:
            return "See HN discussion"

    except subprocess.TimeoutExpired:
        return "See HN discussion"
    except FileNotFoundError:
        return "See HN discussion"  # Fallback when llm not installed
    except Exception as e:
        return "See HN discussion"

import time
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

    # Fallback for long bullet-less summaries (e.g. the Gemini path): shave a line at a time
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

            # Layered summary lookup: local cache -> HN Companion API -> llm/Gemini fallback
            summaries = await resolve_hn_summaries(session, hits)

            # Track consecutive timeouts for early bailout on bad connections
            consecutive_timeouts = 0
            max_consecutive_timeouts = 3  # Stop trying after 3 consecutive timeouts

            for hit in hits:
                title = hit.get("title", "Untitled")
                story_id = hit.get("objectID")
                points = hit.get("points", 0)
                num_comments = hit.get("num_comments", 0)
                author = hit.get("author", "unknown")

                # Format title with upvotes and comments
                formatted_title = f"[{points}↑] {title} ({num_comments}􀌪)"

                if story_id in summaries:
                    summary = summaries[story_id]
                # Fetch discussion summary from LLM with timeout protection
                # Skip LLM calls if we've had too many consecutive timeouts (bad connection)
                elif consecutive_timeouts >= max_consecutive_timeouts:
                    summary = f"{num_comments} comments"
                else:
                    try:
                        summary = await asyncio.wait_for(
                            asyncio.to_thread(get_hn_discussion_summary, story_id),
                            timeout=18.0,  # Allow time for 15s subprocess + overhead
                        )
                        # Reset timeout counter on success
                        if summary != "See HN discussion":
                            consecutive_timeouts = 0
                        else:
                            consecutive_timeouts += 1
                    except asyncio.TimeoutError:
                        consecutive_timeouts += 1
                        summary = f"{num_comments} comments"
                    except Exception:
                        consecutive_timeouts += 1
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


if __name__ == "__main__":
    asyncio.run(main())
