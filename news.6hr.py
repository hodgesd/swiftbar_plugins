#!/Users/hodgesd/PycharmProjects/swiftbar_plugins/.venv/bin/python3.12
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import feedparser
import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel, HttpUrl, validator

ARTICLE_RECENCY_DAYS = 7


class Article(BaseModel):
    title: str
    url: HttpUrl  # Validates URLs
    date: Optional[datetime] = None  # Allows for articles without dates

    # Custom validator to format dates on assignment
    @validator('date', pre=True, allow_reuse=True)
    def parse_date(cls, value):
        for fmt in ('%b. %d, %Y', '%Y-%m-%dT%H:%M:%SZ', '%a, %d %b %Y %H:%M:%S %z', '%Y-%m-%dT%H:%M:%S.%fZ'):
            try:
                return datetime.strptime(value, fmt)
            except (ValueError, TypeError):
                continue
        return None  # Return None if no format matches


class NewsSource(BaseModel):
    url: HttpUrl
    is_html: bool = False
    date_tag: str = 'published'

    def fetch_news(self) -> List[Article]:
        try:
            response = requests.get(self.url)
            response.raise_for_status()
            return self.parse_html(response.text) if self.is_html else self.parse_rss(response.content)
        except requests.RequestException as e:
            print(f"Error fetching source: {e}")
            return []

    def parse_html(self, html_content: str) -> List[Article]:
        soup = BeautifulSoup(html_content, 'html.parser')
        articles = [Article(title=li.find('h1').find('a').text.strip(),
                            url=li.find('h1').find('a')['href'],
                            date=li.find('time').text.strip() if li.find('time') else None)
                    for li in soup.select('ul.article-listing li')]
        return [article for article in articles if self.is_recent(article.date)]

    def parse_rss(self, rss_content: bytes) -> List[Article]:
        feed = feedparser.parse(rss_content)
        articles = [Article(title=entry.title,
                            url=entry.link,
                            date=getattr(entry, self.date_tag, None))
                    for entry in feed.entries]
        return [article for article in articles if self.is_recent(article.date)]

    def is_recent(self, article_date: Optional[datetime]) -> bool:
        if article_date is None:
            return False
        # Ensure datetime.now() is offset-aware by using timezone.utc
        current_time = datetime.now(timezone.utc)
        # Ensure article_date is also offset-aware
        # If article_date is already offset-aware, this will work as expected
        # If it's offset-naive, you might need to adjust based on your data source
        # For example, if you know the timezone or if it's always in UTC
        article_date = article_date.replace(tzinfo=timezone.utc) if article_date.tzinfo is None else article_date
        return current_time - article_date <= timedelta(days=ARTICLE_RECENCY_DAYS)


def main():
    sources = [
        NewsSource(url='https://www.scott.af.mil/News/', is_html=True),
        NewsSource(url='https://daringfireball.net/feeds/articles'),
        NewsSource(url='https://www.apple.com/newsroom/rss-feed.rss', date_tag='updated'),
        NewsSource(url='https://mkennedy.codes/index.xml'),
    ]
    source_names = ["Scott News", "Daring Fireball", "Apple Newsroom", "Michael Kennedy"]
    # The forum feed URL carries a member id + private key, so it lives outside
    # this public repo: CPRT_FEED_URL, or the file CPRT_FEED_URL_FILE (default:
    # the sops-nix secret). Skipped when neither is set.
    cprt_url = os.environ.get("CPRT_FEED_URL", "").strip()
    if not cprt_url:
        try:
            with open(os.environ.get("CPRT_FEED_URL_FILE", "/run/secrets/cprt-feed-url")) as f:
                cprt_url = f.read().strip()
        except OSError:
            pass
    if cprt_url:
        sources.append(NewsSource(url=cprt_url))
        source_names.append("Chief Pilots Forum")

    print('􀤦')
    print("---")
    for name, source in zip(source_names, sources):
        articles = source.fetch_news()
        print(f"{name} | href={source.url}")
        for article in articles:
            formatted_date = article.date.strftime('%-m/%-d/%y') if article.date else 'No date'
            print(f"--[{formatted_date}] {article.title} | href={article.url}")


if __name__ == "__main__":
    main()
