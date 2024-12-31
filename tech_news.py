#!/Users/hodgesd/PycharmProjects/swiftbar_plugins/.venv/bin/python3.12
# <swiftbar.title>Combined Tech News</swiftbar.title>
# <swiftbar.version>v1.3</swiftbar.version>
# <swiftbar.author>Derrick Hodges</swiftbar.author>
# <swiftbar.author.github>hodgesd</hodgesd.author.github>
# <swiftbar.desc>Combines Techmeme, Hacker News, and Lobste.rs in one dropdown</swiftbar.desc>
# <swiftbar.dependencies>python, beautifulsoup4, requests</swiftbar.dependencies>

import asyncio
import requests
from bs4 import BeautifulSoup
from requests import exceptions, get
from sys import exit

# Constants
TECHMEME_URL = "https://www.techmeme.com/"
HN_URL = "https://news.ycombinator.com/"
LOBSTERS_URL = "https://lobste.rs"
LINE_LENGTH = 80
REQUEST_TIMEOUT = 10
MAX_HN_HEADLINES = 10
MAX_LOB_HEADLINES = 15


# Techmeme Scraper
async def getDOM(url):
    response = requests.get(url)
    return BeautifulSoup(response.content, 'html.parser')


async def fetch_techmeme():
    result = await getDOM(TECHMEME_URL)
    stories = result.select('.clus')

    print(f"Techmeme | href={TECHMEME_URL}")

    for story in stories[:15]:
        try:
            story_site = story.select('cite')[0].select('a')[0].text
            story_link = story.select('.ourh')[0]['href']
            story_title = story.select('.ourh')[0].text
            print(f"--{story_title} | href={story_link} tooltip=\"{story_title}\"")
        except Exception:
            pass


# Hacker News Scraper
def fetch_hn():
    print(f"Hacker News | href={HN_URL}")

    try:
        content = get("https://hacker-news.firebaseio.com/v0/topstories.json?print=pretty")
    except exceptions.RequestException:
        print("--⚠️ Error fetching Hacker News")
        return

    ids = content.json()
    story_base = "https://hacker-news.firebaseio.com/v0/item/"

    for id in ids[:MAX_HN_HEADLINES]:
        story = get(story_base + str(id) + ".json").json()
        title = story['title']
        print(f"--{title} | href={HN_URL}item?id={id} tooltip=\"{title}\"")


# Lobste.rs Scraper
def fetch_lobsters():
    try:
        response = requests.get(LOBSTERS_URL, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"--⚠️ Error fetching Lobste.rs: {e}")
        return

    soup = BeautifulSoup(response.content, "html.parser")
    stories = soup.select("ol.stories > li")[:MAX_LOB_HEADLINES]

    print(f"Lobste.rs | href={LOBSTERS_URL}")

    for story in stories:
        title_elem = story.select_one(".link > a.u-url")
        if not title_elem:
            continue
        tags = [tag.text for tag in story.select(".tags > a")]
        first_tag = f"[{tags[0]}] " if tags else ""
        tag_text = f"[{', '.join(tags)}]" if tags else "No tags"
        title = title_elem.text
        url = title_elem['href']
        print(f"--{first_tag}{title} | href={url} tooltip=\"{title} [{tag_text}]\"")


async def main():
    # Menubar Symbol
    print("􀒗")
    print("---")
    await fetch_techmeme()
    fetch_hn()
    fetch_lobsters()
    print("Refresh | refresh=true")


if __name__ == "__main__":
    asyncio.run(main())