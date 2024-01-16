#!/Users/hodgesd/PycharmProjects/swiftbar_plugins/.venv/bin/python3.12

from datetime import datetime

import feedparser
import requests
from bs4 import BeautifulSoup

SCOTT_NEWS_URL = 'https://www.scott.af.mil/News/'
DARING_FIREBALL_RSS_URL = 'https://daringfireball.net/feeds/articles'
APPLE_NEWSROOM_RSS_URL = 'https://www.apple.com/newsroom/rss-feed.rss'
MICHAEL_KENNEDY_RSS_URL = 'https://mkennedy.codes/index.xml'


def format_scott_news_date(date_str):
    """Converts a string date in format 'Jan. 10, 2024' to 'm/d/yy'."""
    dt = datetime.strptime(date_str, '%b. %d, %Y')  # parse the date
    reformatted_date = dt.strftime('%-m/%-d/%y')  # reformat the date
    return reformatted_date


def format_daring_fireball_date(date_str):
    """Converts a string date in format '2024-01-14T00:10:00Z' to 'm/d/yy'."""
    dt = datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%SZ')  # parse the date
    reformatted_date = dt.strftime('%-m/%-d/%y')  # reformat the date
    return reformatted_date


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
            article = {}
            headline = li.find('h1').find('a')
            time_tag = li.find('time')
            article['title'] = headline.get_text(strip=True)
            article['url'] = headline['href']
            article_date = time_tag.get_text(strip=True) if time_tag else 'No date'
            article['date'] = format_scott_news_date(article_date)
            article['summary'] = li.find('div', class_='summary').find('p').get_text(strip=True)
            articles.append(article)
        return articles
    else:
        return [{'title': 'Error fetching news', 'url': '#', 'summary': 'Error fetching response'}]


def fetch_news_from_rss(url):
    response = fetch_response(url)
    if response:
        feed = feedparser.parse(response.content)
        if not feed.entries:
            print("No entries found in feed. Feed might be empty or not properly parsed.")
            return []
        articles = []
        for entry in feed.entries:
            if 'title' in entry and 'link' in entry:
                entry_date = entry.get('published', 'No date')
                date = format_daring_fireball_date(entry_date)
                articles.append({
                    'title': entry.title,
                    'url': entry.link,
                    'date': date
                })
        return articles
    else:
        print("Could not fetch RSS data.")
        return []


def fetch_news_from_atom_rss(rss_url):
    news_entries = []
    response = requests.get(rss_url)
    soup = BeautifulSoup(response.content, 'html.parser')

    for entry in soup.find_all('entry'):
        title = entry.title.text.strip()
        link = entry.link['href']
        published_raw = entry.updated.text
        # Convert published date to the required format
        published_date = datetime.strptime(published_raw, '%Y-%m-%dT%H:%M:%S.%fZ')
        title_date = published_date.strftime('%m/%d/%y')
        news_entries.append({
            'title': title,
            'url': link,
            'date': title_date
        })
    return news_entries


def fetch_news_from_rss_2_0(url):
    response = fetch_response(url)
    if response:
        feed = feedparser.parse(response.content)
        articles = []
        for item in feed.entries:
            title = item.title
            link = item.link
            pub_date = item.published
            date = datetime.strptime(pub_date, '%a, %d %b %Y %H:%M:%S %z').strftime('%m/%d/%y')
            articles.append({
                'title': title,
                'url': link,
                'date': date
            })
        return articles
    else:
        return [{'title': 'Error fetching news', 'url': '#', 'summary': 'Error fetching response'}]


def main():
    scott_articles = fetch_news_from_html(SCOTT_NEWS_URL)
    daring_articles = fetch_news_from_rss(DARING_FIREBALL_RSS_URL)
    apple_articles = fetch_news_from_atom_rss(APPLE_NEWSROOM_RSS_URL)

    print('􀤦')
    print("---")
    # Scott News Submenu
    print(f"Scott News | href={SCOTT_NEWS_URL}")
    for article in scott_articles:
        print(f"--[{article['date']}] {article['title']} | href={article['url']}")
    print("---")
    # Daring Fireball Submenu
    print(f"Daring Fireball {len(daring_articles)} | href={DARING_FIREBALL_RSS_URL}")
    for article in daring_articles:
        if not article['title'].startswith('[Sponsor]'):
            print(f"--[{article['date']}] {article['title']} | href={article['url']}")

    # Apple Newsroom Submenu
    print(f"Apple Newsroom {len(apple_articles)} | href={APPLE_NEWSROOM_RSS_URL}")
    for article in apple_articles:
        print(f"--[{article['date']}] {article['title']} | href={article['url']}")

    # Michael Kennedy's Articles
    michael_kennedy_articles = fetch_news_from_rss_2_0(MICHAEL_KENNEDY_RSS_URL)
    print("---")
    print(f"Michael Kennedy | href={MICHAEL_KENNEDY_RSS_URL}")
    for article in michael_kennedy_articles:
        print(f"--[{article['date']}] {article['title']} | href={article['url']}")


if __name__ == "__main__":
    main()
