#!/Users/hodgesd/PycharmProjects/swiftbar_plugins/.venv/bin/python3.12

from datetime import datetime, timedelta

import feedparser
import requests
from bs4 import BeautifulSoup

ARTICLE_RECENCY_DAYS = 30

# URLs
SCOTT_NEWS_URL = 'https://www.scott.af.mil/News/'
DARING_FIREBALL_RSS_URL = 'https://daringfireball.net/feeds/articles'
APPLE_NEWSROOM_RSS_URL = 'https://www.apple.com/newsroom/rss-feed.rss'
MICHAEL_KENNEDY_RSS_URL = 'https://mkennedy.codes/index.xml'


def format_date(date_str):
    for fmt in ('%b. %d, %Y', '%Y-%m-%dT%H:%M:%SZ', '%a, %d %b %Y %H:%M:%S %z', '%Y-%m-%dT%H:%M:%S.%fZ'):
        try:
            return datetime.strptime(date_str, fmt).strftime('%-m/%-d/%y')
        except ValueError:
            continue
    return 'Invalid date'


def fetch_response(url):
    try:
        response = requests.get(url)
        response.raise_for_status()
        return response
    except requests.RequestException as e:
        print(f"Error fetching source: {e}")
        return None


def is_recent_article(article_date_str, days=30):
    try:
        article_date = datetime.strptime(article_date_str, '%m/%d/%y')
        return datetime.now() - article_date <= timedelta(days=days)
    except ValueError:
        return False


def fetch_news(url, is_html=False, date_tag='published'):
    response = fetch_response(url)
    if not response:
        return [{'title': 'Error fetching news', 'url': '#', 'summary': 'Error fetching response'}]

    articles = []
    if is_html:
        soup = BeautifulSoup(response.text, 'html.parser')
        for li in soup.select('ul.article-listing li'):
            headline = li.find('h1').find('a')
            time_tag = li.find('time')
            article_date = format_date(time_tag.get_text(strip=True) if time_tag else 'No date')
            if is_recent_article(article_date) and '[Sponsor]' not in headline.get_text():
                articles.append({
                    'title': headline.get_text(strip=True),
                    'url': headline['href'],
                    'date': article_date
                })
    else:
        feed = feedparser.parse(response.content)
        for entry in feed.entries:
            article_date = format_date(getattr(entry, date_tag, 'No date'))
            if is_recent_article(article_date) and '[Sponsor]' not in entry.title:
                articles.append({
                    'title': entry.title,
                    'url': entry.link,
                    'date': article_date
                })

    return articles


def main():
    scott_articles = fetch_news(SCOTT_NEWS_URL, is_html=True)
    daring_articles = fetch_news(DARING_FIREBALL_RSS_URL)
    apple_articles = fetch_news(APPLE_NEWSROOM_RSS_URL, date_tag='updated')
    michael_kennedy_articles = fetch_news(MICHAEL_KENNEDY_RSS_URL)

    print('􀤦')
    print("---")
    for name, articles, url in [
        ("Scott News", scott_articles, SCOTT_NEWS_URL),
        ("Daring Fireball", daring_articles, DARING_FIREBALL_RSS_URL),
        ("Apple Newsroom", apple_articles, APPLE_NEWSROOM_RSS_URL),
        ("Michael Kennedy", michael_kennedy_articles, MICHAEL_KENNEDY_RSS_URL)
    ]:
        print(f"{name} | href={url}")
        for article in articles:
            print(f"--[{article['date']}] {article['title']} | href={article['url']}")


if __name__ == "__main__":
    main()
