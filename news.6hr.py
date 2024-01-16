#!/Users/hodgesd/PycharmProjects/swiftbar_plugins/.venv/bin/python3.12

from datetime import datetime

import feedparser
import requests
from bs4 import BeautifulSoup

SCOTT_NEWS_URL = 'https://www.scott.af.mil/News/'
DARING_FIREBALL_RSS_URL = 'https://daringfireball.net/feeds/articles'
APPLE_NEWSROOM_RSS_URL = 'https://www.apple.com/newsroom/rss-feed.rss'
MICHAEL_KENNEDY_RSS_URL = 'https://mkennedy.codes/index.xml'

DATE_FORMATS = {
    'Scott': '%b. %d, %Y',
    'RSS': '%Y-%m-%dT%H:%M:%SZ',
    'RSS 2.0': '%a, %d %b %Y %H:%M:%S %z',
    'Atom': '%Y-%m-%dT%H:%M:%S.%fZ'
}


def format_date(date_str, source_type):
    if date_str == 'No date':
        return date_str
    try:
        dt = datetime.strptime(date_str, DATE_FORMATS[source_type])
        return dt.strftime('%-m/%-d/%y')
    except ValueError:
        print(f"Error parsing date: {date_str} for {source_type}")
        return 'Invalid date'


def fetch_response(url):
    try:
        response = requests.get(url)
        response.raise_for_status()
        return response
    except requests.RequestException as e:
        print(f"Error fetching source: {e}")
        return None


def fetch_news_from_html(url):
    response = fetch_response(url)
    if response:
        soup = BeautifulSoup(response.text, 'html.parser')
        articles = []
        for li in soup.select('ul.article-listing li'):
            headline = li.find('h1').find('a')
            time_tag = li.find('time')
            articles.append({
                'title': headline.get_text(strip=True),
                'url': headline['href'],
                'date': format_date(time_tag.get_text(strip=True) if time_tag else 'No date', 'Scott')
            })
        return articles
    return [{'title': 'Error fetching news', 'url': '#', 'summary': 'Error fetching response'}]


def fetch_news_from_rss(url, rss_type):
    response = fetch_response(url)
    if response:
        feed = feedparser.parse(response.content)
        if not feed.entries:
            print("No entries found in feed. Feed might be empty or not properly parsed.")
            return []
        articles = []
        for entry in feed.entries:
            date_tag = 'published' if rss_type != 'Atom' else 'updated'
            entry_date = getattr(entry, date_tag, 'No date')
            articles.append({
                'title': entry.title,
                'url': entry.link,
                'date': format_date(entry_date, rss_type)
            })
        return articles
    return [{'title': 'Error fetching news', 'url': '#', 'summary': 'Error fetching response'}]


def main():
    scott_articles = fetch_news_from_html(SCOTT_NEWS_URL)
    daring_articles = fetch_news_from_rss(DARING_FIREBALL_RSS_URL, 'RSS')
    apple_articles = fetch_news_from_rss(APPLE_NEWSROOM_RSS_URL, 'Atom')
    michael_kennedy_articles = fetch_news_from_rss(MICHAEL_KENNEDY_RSS_URL, 'RSS 2.0')

    print('􀤦')
    print("---")
    # Scott News Submenu
    print(f"Scott News | href={SCOTT_NEWS_URL}")
    for article in scott_articles:
        print(f"--[{article['date']}] {article['title']} | href={article['url']}")
    print("---")
    # Daring Fireball Submenu
    print(f"Daring Fireball | href={DARING_FIREBALL_RSS_URL}")
    for article in daring_articles:
        print(f"--[{article['date']}] {article['title']} | href={article['url']}")

    # Apple Newsroom Submenu
    print(f"Apple Newsroom | href={APPLE_NEWSROOM_RSS_URL}")
    for article in apple_articles:
        print(f"--[{article['date']}] {article['title']} | href={article['url']}")

    # Michael Kennedy's Articles
    print(f"Michael Kennedy | href={MICHAEL_KENNEDY_RSS_URL}")
    for article in michael_kennedy_articles:
        print(f"--[{article['date']}] {article['title']} | href={article['url']}")


if __name__ == "__main__":
    main()
