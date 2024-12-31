#!/Users/hodgesd/PycharmProjects/swiftbar_plugins/.venv/bin/python3.12

# <xbar.title>St. Louis Today Headlines</xbar.title>
# <xbar.version>v1.0</xbar.version>
# <xbar.author>Derrick Hodges</xbar.author>
# <xbar.author.github>YourGitHub</xbar.github>
# <xbar.desc>Scrapes headlines from STLToday.com and displays them in SwiftBar.</xbar.desc>
# <xbar.dependencies>python,requests,bs4</xbar.dependencies>

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Set
import requests
from bs4 import BeautifulSoup
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

    def _extract_article(self, article_elem, category):
        try:
            title_elem = article_elem.select_one('.card-headline a, .tnt-headline a, .tnt-asset-link')
            headline = title_elem.get('aria-label') if title_elem and title_elem.has_attr('aria-label') else title_elem.get_text(strip=True)
            link = title_elem['href'] if title_elem else ''
            summary_elem = article_elem.select_one('div.card-lead p')
            summary = summary_elem.get_text(strip=True) if summary_elem else "No summary"

            article = Article(
                headline=headline,
                link=link,
                summary=summary,
                category=category
            ).with_full_link()

            # print(f"Article found: {headline} | Category: {category} | Link: {article.link} | color=green")

            return article
        except Exception as e:
            print(f"Error extracting article: {e} | color=red")
            return None

    def get_headlines(self) -> list[Article]:
        try:
            response = self.session.get("https://www.stltoday.com", timeout=20)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            articles = []

            blocks = soup.select('section.block')
            for block in blocks:
                category_elem = block.select_one('div.block-title-inner h3')
                category = category_elem.get_text(strip=True) if category_elem else 'Uncategorized'
                # print(f"Found category: {category} | color=blue")

                card_grid = block.select('article')
                # print(f"Category: {category} | Articles found: {len(card_grid)} | color=yellow")

                for article_elem in card_grid:
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

    print("STL")
    print("---")

    if not articles:
        print("No headlines found. | color=red")
        print("---")
        print("Visit STLToday | href=https://www.stltoday.com")
        return

    for article in articles[:90]:  # Display top 10 articles
        print(format_menu_item(article))

    print("---")
    print(f"Last Updated: {datetime.now().strftime('%I:%M %p')} | color=gray")
    print("Visit STLToday | href=https://www.stltoday.com")
    print("Refresh | refresh=true")

if __name__ == "__main__":
    main()