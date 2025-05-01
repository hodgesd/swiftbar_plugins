#!/Users/hodgesd/PycharmProjects/swiftbar_plugins/.venv/bin/python3.12
# <xbar.title>Lobste.rs Headlines</xbar.title>
# <xbar.version>v1.6</xbar.version>
# <xbar.author>Derrick Hodges</xbar.author>
# <xbar.author.github>hodgesd</xbar.author.github>
# <xbar.desc>Displays latest 15 headlines from Lobste.rs with first tag prefix and full tags in tooltips</xbar.desc>
# <xbar.dependencies>python, beautifulsoup4, requests</xbar.dependencies>
# <xbar.abouturl>https://lobste.rs</xbar.abouturl>

import requests
from bs4 import BeautifulSoup

LOBSTERS_URL = "https://lobste.rs"
LINE_LENGTH = 80
REQUEST_TIMEOUT = 10
MAX_HEADLINES = 15


def fetch_lobsters_headlines():
    try:
        response = requests.get(LOBSTERS_URL, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as e:
        return [f"⚠️ Error fetching Lobste.rs: {e}"]

    soup = BeautifulSoup(response.content, "html.parser")
    stories = soup.select("ol.stories > li")[:MAX_HEADLINES]  # Limit to 15 headlines

    return [
        format_headline(story)
        for story in stories
        if (story.select_one(".link > a.u-url"))
    ]


def format_headline(story):
    title_elem = story.select_one(".link > a.u-url")
    tags = [tag.text for tag in story.select(".tags > a")]
    first_tag = f"[{tags[0]}] " if tags else ""
    tag_text = f"[{', '.join(tags)}]" if tags else "No tags"

    title = title_elem.text
    url = title_elem['href']
    display_title = title if len(title) <= LINE_LENGTH else f"{title[:LINE_LENGTH]}..."

    return f"{first_tag}{display_title} | href={url} tooltip=\"{title} - Tags: {tag_text}\""


def main():
    print("🦞")  # Lobster emoji as the icon
    print("---")
    print(f"Lobste.rs Home | href={LOBSTERS_URL}\n---")

    for headline in fetch_lobsters_headlines():
        print(headline)

    print("---")
    print("Refresh | refresh=true")


if __name__ == "__main__":
    main()
