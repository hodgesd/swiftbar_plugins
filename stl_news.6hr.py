from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Set

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter, Retry


@dataclass
class Article:
    headline: str
    link: str
    summary: str = ''
    category: str = ''

    def with_full_link(self) -> 'Article':
        if not self.link.startswith('http'):
            self.link = f"https://www.stltoday.com{self.link}"
        return self


class STLScraper:
    def __init__(self):
        self.session = self._setup_session()
        self.seen_links: Set[str] = set()

    @staticmethod
    def _setup_session() -> requests.Session:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
        }
        session = requests.Session()
        session.headers.update(headers)

        retry_strategy = Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount('https://', adapter)
        session.mount('http://', adapter)

        return session

    def _extract_article(self, element: Tag, category: str = '') -> Optional[Article]:
        # Extract headline
        headline_elem = element.select_one('.card-headline a')
        if not headline_elem:
            return None

        headline = headline_elem.text.strip()
        link = headline_elem['href']

        # Extract summary (tooltip)
        summary_elem = element.select_one('.card-lead p')
        summary = summary_elem.text.strip() if summary_elem else ''

        # Mark as seen to avoid duplicates
        if link in self.seen_links:
            return None
        self.seen_links.add(link)

        return Article(
            headline=headline,
            link=link,
            summary=summary,
            category=category
        ).with_full_link()

    def get_headlines(self) -> list[Article]:
        try:
            response = self.session.get("https://www.stltoday.com", timeout=20)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')
            articles = []

            # Iterate over each block to extract category and articles
            blocks = soup.select('div.block-title-inner')
            for block in blocks:
                # Extract category name
                category_elem = block.select_one('h3')
                category = category_elem.get_text(" ", strip=True) if category_elem else 'Uncategorized'

                # Find the parent section containing articles
                section = block.find_parent('section', class_='block')
                if section:
                    for article_elem in section.select('article.tnt-asset-type-article'):
                        if article := self._extract_article(article_elem, category):
                            articles.append(article)

            return articles
        except requests.RequestException as e:
            print(f"Error fetching STLToday: {e} | color=red")
            return []

def format_menu_item(article: Article, truncate_length: int = 75) -> str:
    display_headline = f"[{article.category}] {article.headline}"
    if len(display_headline) > truncate_length:
        display_headline = f"{display_headline[:truncate_length-3]}..."

    return f'{display_headline} | href={article.link} tooltip="{article.summary}"'


def main():
    scraper = STLScraper()
    articles = scraper.get_headlines()

    if not articles:
        print("No headlines found. | color=red")
        print("---")
        print("Visit STLToday | href=https://www.stltoday.com")
        return

    print("STL Today Headlines")
    print("---")

    for article in articles[:10]:
        print(format_menu_item(article))

    print("---")
    print(f"Last Updated: {datetime.now().strftime('%I:%M %p')} | color=gray")
    print("Visit STLToday | href=https://www.stltoday.com")
    print("Refresh | refresh=true")


if __name__ == "__main__":
    main()