#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "aiohttp>=3.8.0",
#     "beautifulsoup4>=4.9.0",
#     "requests>=2.25.0",
# ]
# ///

# <swiftbar.title>Combined Tech News</swiftbar.title>
# <swiftbar.version>v1.5</swiftbar.version>
# <swiftbar.author>Derrick Hodges</swiftbar.author>
# <swiftbar.author.github>hodgesd</swiftbar.author.github>
# <swiftbar.desc>Combines Techmeme, Hacker News, Lobste.rs, STLToday, and BND in one dropdown</swiftbar.desc>
# <swiftbar.dependencies>uv, beautifulsoup4, aiohttp, requests</swiftbar.dependencies>

import asyncio
import datetime
import re
from dataclasses import dataclass
from typing import Set, Optional

import aiohttp
import requests
from aiohttp import ClientTimeout
from bs4 import BeautifulSoup, Tag
import time
from io import StringIO
from requests.adapters import HTTPAdapter, Retry

# Constants
TECHMEME_URL = "https://www.techmeme.com/"
HN_URL = "https://news.ycombinator.com/"
LOBSTERS_URL = "https://lobste.rs"
STLTODAY_URL = "https://www.stltoday.com"
BND_URL = "https://www.bnd.com"
REQUEST_TIMEOUT = 10
MAX_HEADLINES = 15
TRIM_LENGTH = 80  # Character limit for headlines

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
        return f"--⚠️ Error fetching {url}: {e}\n"


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

    # Use full title (no trimming)
    full_title = f"{tags_text}{title}"

    # Use summary as tooltip if available, otherwise use title
    tooltip_text = summary if summary else title
    # Escape quotes and backslashes for SwiftBar tooltip format
    tooltip_text = tooltip_text.replace('\\', '\\\\').replace('"', '\\"')

    return f"--{full_title} | href={url} tooltip=\"{tooltip_text}\" trim=false\n"


def format_stl_headline(article: Article, truncate_length: int = 75) -> str:
    """Format STLToday article for SwiftBar menu display"""
    display_headline = f"[{article.category}] {article.headline}"
    if len(display_headline) > truncate_length:
        display_headline = f"{display_headline[:truncate_length-3]}..."

    # Escape quotes in tooltip
    tooltip_text = article.summary.replace('\\', '\\\\').replace('"', '\\"')
    return f"--{display_headline} | href={article.link} tooltip=\"{tooltip_text}\"\n"


def format_bnd_headline(article: Article, truncate_length: int = 75) -> str:
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

    if len(display_headline) > truncate_length:
        display_headline = f"{display_headline[:truncate_length-3]}..."

    # Escape quotes in tooltip
    tooltip_text = tooltip_text.replace('\\', '\\\\').replace('"', '\\"')
    return f'--{display_headline} | href={article.link} tooltip="{tooltip_text}"\n'


async def fetch_techmeme(buffer=None):
    if buffer is None:
        buffer = StringIO()
    result = await getDOM(TECHMEME_URL)
    if isinstance(result, str):
        buffer.write(result)
        return

    stories = result.select('.clus')
    buffer.write(f"Techmeme | href={TECHMEME_URL}\n")
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


async def fetch_hn(buffer=None):
    if buffer is None:
        buffer = StringIO()
    # Use Algolia API for richer metadata in a single request
    algolia_url = "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=15"
    buffer.write(f"Hacker News | href={HN_URL}\n")

    try:
        timeout = ClientTimeout(total=REQUEST_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(algolia_url) as response:
                response.raise_for_status()
                data = await response.json()

                for hit in data.get('hits', [])[:MAX_HEADLINES]:
                    title = hit.get('title', 'Untitled')
                    story_id = hit.get('objectID')
                    points = hit.get('points', 0)
                    num_comments = hit.get('num_comments', 0)
                    author = hit.get('author', 'unknown')

                    # Format title with metadata prefix
                    formatted_title = f"[{points}↑|{num_comments}#] {title}"

                    # Tooltip shows author
                    summary = f"by {author}"

                    buffer.write(format_headline(formatted_title, f"{HN_URL}item?id={story_id}", summary=summary))
    except Exception as e:
        buffer.write(f"--⚠️ Error fetching HN: {e}\n")


async def fetch_lobsters(buffer=None):
    if buffer is None:
        buffer = StringIO()
    result = await getDOM(LOBSTERS_URL)
    if isinstance(result, str):
        buffer.write(result)
        return

    stories = result.select("ol.stories > li")[:MAX_HEADLINES]
    buffer.write(f"Lobste.rs | href={LOBSTERS_URL}\n")

    for story in stories:
        try:
            title_elem = story.select_one(".link > a.u-url")
            if not title_elem:
                continue
            title = title_elem.text
            url = title_elem['href']
            tags = [tag.text for tag in story.select(".tags > a")]

            # Try to extract description if available
            summary = ''
            desc_elem = story.select_one(".description")
            if desc_elem:
                summary = desc_elem.text.strip()

            buffer.write(format_headline(title, url, tags, summary))
        except Exception:
            continue


async def fetch_stltoday(buffer=None):
    if buffer is None:
        buffer = StringIO()
    
    buffer.write(f"STLToday | href={STLTODAY_URL}\n")
    
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
            for article in articles:
                buffer.write(format_stl_headline(article))
                
    except Exception as e:
        buffer.write(f"--⚠️ Error fetching STLToday: {e}\n")


async def fetch_bnd(buffer=None):
    if buffer is None:
        buffer = StringIO()
    
    buffer.write(f"BND | href={BND_URL}\n")
    
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
        buffer.write(f"--⚠️ Error fetching BND: {e}\n")


async def main():
    # Menubar Symbol
    print("􀤦")
    print("---")

    start = time.time()

    techmeme, hn, lobsters, stltoday, bnd = await asyncio.gather(
        fetch_and_buffer(fetch_techmeme),
        fetch_and_buffer(fetch_hn),
        fetch_and_buffer(fetch_lobsters),
        fetch_and_buffer(fetch_stltoday),
        fetch_and_buffer(fetch_bnd)
    )

    # Print each section sequentially
    print(techmeme)
    print(hn)
    print(lobsters)
    print(stltoday)
    print(bnd)

    end = time.time()
    print("---")
    print(f"Updated at {datetime.datetime.now().strftime('%I:%M %p')} (fetched in {round(end - start, 2)}s)")
    print("Refresh | refresh=true")


if __name__ == "__main__":
    asyncio.run(main())
