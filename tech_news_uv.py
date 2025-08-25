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
# <swiftbar.version>v1.3</swiftbar.version>
# <swiftbar.author>Derrick Hodges</swiftbar.author>
# <swiftbar.author.github>hodgesd</swiftbar.author.github>
# <swiftbar.desc>Combines Techmeme, Hacker News, and Lobste.rs in one dropdown</swiftbar.desc>
# <swiftbar.dependencies>uv, beautifulsoup4, aiohttp, requests</swiftbar.dependencies>

import asyncio
import datetime

import aiohttp
import requests
from aiohttp import ClientTimeout
from bs4 import BeautifulSoup
import time
from io import StringIO

# Constants
TECHMEME_URL = "https://www.techmeme.com/"
HN_URL = "https://news.ycombinator.com/"
LOBSTERS_URL = "https://lobste.rs"
REQUEST_TIMEOUT = 10
MAX_HEADLINES = 15
TRIM_LENGTH = 80  # Character limit for headlines


async def getDOM(url):
    timeout = ClientTimeout(total=REQUEST_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout, connector=aiohttp.TCPConnector(ssl=False)) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                return BeautifulSoup(await response.text(), 'html.parser')
    except Exception as e:
        return f"--⚠️ Error fetching {url}: {e}\n"


async def fetch_and_buffer(scraper):
    buffer = StringIO()
    await scraper(buffer)
    return buffer.getvalue()


def format_headline(title, url, tags=None):
    """Format headlines with trimming and tooltips."""
    tags_text = f"[{', '.join(tags)}] " if tags else ""
    trimmed_title = title[:TRIM_LENGTH] + '…' if len(title) > TRIM_LENGTH else title
    return f"--{tags_text}{trimmed_title} | href={url} tooltip=\"{title}\" length={TRIM_LENGTH} trim=true\n"


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
            buffer.write(format_headline(story_title, story_link))
        except Exception:
            continue


async def fetch_hn(buffer=None):
    if buffer is None:
        buffer = StringIO()
    ids_url = "https://hacker-news.firebaseio.com/v0/topstories.json?print=pretty"
    buffer.write(f"Hacker News | href={HN_URL}\n")

    try:
        ids = await asyncio.to_thread(lambda: requests.get(ids_url).json())
        story_base = "https://hacker-news.firebaseio.com/v0/item/"

        for id in ids[:MAX_HEADLINES]:
            story = await asyncio.to_thread(lambda: requests.get(story_base + str(id) + ".json").json())
            title = story.get('title', 'Untitled')
            buffer.write(format_headline(title, f"{HN_URL}item?id={id}"))
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
            buffer.write(format_headline(title, url, tags))
        except Exception:
            continue


async def main():
    # Menubar Symbol
    print("􀒗")
    print("---")

    start = time.time()

    techmeme, hn, lobsters = await asyncio.gather(
        fetch_and_buffer(fetch_techmeme),
        fetch_and_buffer(fetch_hn),
        fetch_and_buffer(fetch_lobsters)
    )

    # Print each section sequentially
    print(techmeme)
    print(hn)
    print(lobsters)

    end = time.time()
    print("---")
    print(f"Updated at {datetime.datetime.now().strftime('%I:%M %p')} (fetched in {round(end - start, 2)}s)")
    print("Refresh | refresh=true")


if __name__ == "__main__":
    asyncio.run(main())
