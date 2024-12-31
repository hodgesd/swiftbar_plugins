#!/Users/hodgesd/PycharmProjects/swiftbar_plugins/.venv/bin/python3.12
# <swiftbar.title>Combined Tech News</swiftbar.title>
# <swiftbar.version>v1.3</swiftbar.version>
# <swiftbar.author>Derrick Hodges</swiftbar.author>
# <swiftbar.author.github>hodgesd</swiftbar.author.github>
# <swiftbar.desc>Combines Techmeme, Hacker News, and Lobste.rs in one dropdown</swiftbar.desc>
# <swiftbar.dependencies>python, beautifulsoup4, requests</swiftbar.dependencies>

import asyncio

import aiohttp
import requests
from aiohttp import ClientTimeout
from bs4 import BeautifulSoup

# Constants
TECHMEME_URL = "https://www.techmeme.com/"
HN_URL = "https://news.ycombinator.com/"
LOBSTERS_URL = "https://lobste.rs"
LINE_LENGTH = 80
REQUEST_TIMEOUT = 10
MAX_HEADLINES = 15


async def getDOM(url):
    timeout = ClientTimeout(total=REQUEST_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout, connector=aiohttp.TCPConnector(ssl=False)) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                return BeautifulSoup(await response.text(), 'html.parser')
    except Exception as e:
        print(f"--⚠️ Error fetching {url}: {e}")
        return None


async def fetch_techmeme():
    result = await getDOM(TECHMEME_URL)
    if not result:
        return

    stories = result.select('.clus')
    print(f"Techmeme | href={TECHMEME_URL}")
    for story in stories[:MAX_HEADLINES]:
        try:
            story_site = story.select_one('cite a').text
            story_link = story.select_one('.ourh')['href']
            story_title = story.select_one('.ourh').text
            print(f"--{story_title} | href={story_link} tooltip=\"{story_title}\"")
        except Exception:
            continue


async def fetch_hn():
    hn_headlines = []
    ids_url = "https://hacker-news.firebaseio.com/v0/topstories.json?print=pretty"
    print(f"Hacker News | href={HN_URL}")

    try:
        ids = await asyncio.to_thread(lambda: requests.get(ids_url).json())
        story_base = "https://hacker-news.firebaseio.com/v0/item/"

        for id in ids[:MAX_HEADLINES]:
            story = await asyncio.to_thread(lambda: requests.get(story_base + str(id) + ".json").json())
            title = story.get('title', 'Untitled')
            hn_headlines.append(f"--{title} | href={HN_URL}item?id={id} tooltip=\"{title}\"")
    except Exception as e:
        hn_headlines.append(f"--⚠️ Error fetching HN: {e}")

    # Print the headlines at the end to keep them in the right section
    for headline in hn_headlines:
        print(headline)

async def fetch_lobsters():
    result = await getDOM(LOBSTERS_URL)
    if not result:
        return

    stories = result.select("ol.stories > li")[:MAX_HEADLINES]
    print(f"Lobste.rs | href={LOBSTERS_URL}")

    for story in stories:
        try:
            title_elem = story.select_one(".link > a.u-url")
            if not title_elem:
                continue
            tags = [tag.text for tag in story.select(".tags > a")]
            first_tag = f"[{tags[0]}] " if tags else ""
            tag_text = f"[{', '.join(tags)}]" if tags else "No tags"
            title = title_elem.text
            url = title_elem['href']
            print(f"--{first_tag}{title} | href={url} tooltip=\"{title} [{tag_text}]\"")
        except Exception as e:
            continue


async def main():
    # Menubar Symbol
    print("􀒗")
    print("---")

    # Fetch each site sequentially
    await fetch_techmeme()
    await fetch_hn()
    await fetch_lobsters()

    print("---")
    print("Refresh | refresh=true")

if __name__ == "__main__":
    asyncio.run(main())