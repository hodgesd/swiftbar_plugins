#!/Users/hodgesd/PycharmProjects/swiftbar_plugins/.venv/bin/python3.12
# <xbar.title>Lobste.rs Headlines</xbar.title>
# <xbar.version>v1.6</xbar.version>
# <xbar.author>Derrick Hodges</xbar.author>
# <xbar.author.github>hodgesd</xbar.author.github>
# <xbar.desc>Displays latest headlines from Lobste.rs with aligned tags using box drawing</xbar.desc>
# <xbar.dependencies>python, beautifulsoup4, requests</xbar.dependencies>
# <xbar.abouturl>https://lobste.rs</xbar.abouturl>

import requests
from bs4 import BeautifulSoup

LOBSTERS_URL = "https://lobste.rs"
LINE_LENGTH = 80
REQUEST_TIMEOUT = 10


def fetch_lobsters_headlines():
    try:
        response = requests.get(LOBSTERS_URL, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        return [f"⚠️ Error fetching Lobste.rs: {e}"]

    soup = BeautifulSoup(response.content, "html.parser")
    stories = soup.select("ol.stories > li")

    return [
        extract_headline_data(story)
        for story in stories if story.select_one(".link > a.u-url")
    ]


def extract_headline_data(story):
    title_elem = story.select_one(".link > a.u-url")
    tags_elem = story.select(".tags > a")

    title = title_elem.text
    url = title_elem['href']
    tags = [tag.text for tag in tags_elem]
    tag_text = f"[{', '.join(tags)}]" if tags else "No tags"

    return (title, url, tag_text)


def format_headline(item):
    title, url, tag_text = item
    display_title = (title[:LINE_LENGTH] + '...') if len(title) > LINE_LENGTH else title
    return f"{display_title} | href={url} tooltip=\"{title} - Tags: {tag_text}\""


def main():
    print("🦞")  # Lobster emoji as the icon
    print("---")
    print(f"Lobste.rs Home | href={LOBSTERS_URL}\n---")

    headlines = fetch_lobsters_headlines()
    for headline in headlines:
        print(format_headline(headline))

    print("---")
    print("Refresh | refresh=true")


if __name__ == "__main__":
    main()