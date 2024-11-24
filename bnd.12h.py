#!/Users/hodgesd/PycharmProjects/swiftbar_plugins/.venv/bin/python3.12

# <xbar.title>Belleville News Dispatch Headlines</xbar.title>
# <xbar.version>v1.1</xbar.version>
# <xbar.author>MajorDouble</xbar.author>
# <xbar.author.github>YourGitHub</xbar.github>
# <xbar.desc>Bnd.com headlines with enhanced timeout handling and debugging.</xbar.desc>
# <xbar.dependencies>python,requests,bs4</xbar.dependencies>

from __future__ import annotations

import re
import time
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
    category: str = ''

    def with_full_link(self) -> Article:
        """Ensure article has full URL"""
        if not self.link.startswith('http'):
            self.link = f"https://www.bnd.com{self.link}"
        return self

CATEGORY_ABBREVIATIONS = {
    'Opinion Columns & Blogs': '[Op Ed]',
    'High School Football': '[HS Football]',
    'Crime': '[Crime]',
    'Metro-East News': '[Metro]',
    'Business': '[Biz]',
    'Food & Drink': '[Food]',
    'St. Louis Cardinals': '[Cards]',
    'Belleville': '[BLV]',
    'Latest News': '[Latest]'
}

class BNDScraper:
    def __init__(self):
        self.session = self._setup_session()
        self.seen_links: Set[str] = set()

    @staticmethod
    def _setup_session() -> requests.Session:
        """Setup requests session with retry logic and headers"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Cache-Control': 'max-age=0',
            'Referer': 'https://www.google.com/',
            'Origin': 'https://www.bnd.com',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'cross-site'
        }

        session = requests.Session()
        session.headers.update(headers)

        retry_strategy = Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount('https://', adapter)
        session.mount('http://', adapter)

        return session

    def _normalize_link(self, link: str) -> str:
        """Normalize link by removing fragments and ensuring full URL"""
        # Remove fragment identifier (everything after #)
        base_link = link.split('#')[0]

        # Ensure full URL
        if not base_link.startswith('http'):
            base_link = f"https://www.bnd.com{base_link}"

        return base_link

    def _extract_article_from_element(self, element: Tag, category: str = '') -> Optional[Article]:
        """Extract article information from HTML element"""
        headline_elem = element.find('h3')
        if not headline_elem or not (link_elem := headline_elem.find('a')):
            return None

        link = link_elem.get('href', '')
        normalized_link = self._normalize_link(link)

        if not link or normalized_link in self.seen_links:
            return None

        self.seen_links.add(normalized_link)

        if not category:
            kicker = element.find(class_='kicker')
            category = self._clean_text(kicker.text) if kicker else ''

        return Article(
            headline=self._clean_text(headline_elem.text),
            link=link,  # Keep original link for display
            category=category
        ).with_full_link()
    @staticmethod
    def _clean_text(text: str) -> str:
        """Clean whitespace and newlines from text"""
        return re.sub(r'\s+', ' ', text).strip()

    def get_headlines(self) -> list[Article]:
        """Fetch and parse headlines from BND website"""
        try:
            time.sleep(1)  # Be nice to the server
            response = self.session.get("https://www.bnd.com", timeout=20, verify=True)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')
            articles = []

            # Get main grid articles
            if content_area := soup.find('section', class_='grid'):
                for article_elem in content_area.find_all('article', recursive=True):
                    if not article_elem.find_parent(class_='partner-digest-group'):
                        if article := self._extract_article_from_element(article_elem):
                            articles.append(article)

            # Get latest news articles
            if latest_section := soup.find('div', attrs={'data-tb-region': 'latest'}):
                for article_elem in latest_section.find_all('div', class_='package'):
                    if article := self._extract_article_from_element(article_elem, category='Latest News'):
                        articles.append(article)

            return articles

        except requests.Timeout:
            print("Timeout while fetching headlines. Retrying in 5 minutes... | color=red")
        except requests.RequestException as e:
            print(f"Request error: {str(e)} | color=red")
        except Exception as e:
            print(f"An unexpected error occurred: {str(e)} | color=red")
        return []

def format_menu_item(article: Article, truncate_length: int = 75) -> str:
    """Format article for SwiftBar menu display"""
    display_headline = article.headline
    if article.category:
        abbreviated = CATEGORY_ABBREVIATIONS.get(article.category, f'[{article.category}]')
        display_headline = f"{abbreviated} {article.headline}"
        tooltip_text = f"{article.category}: {article.headline}"
    else:
        tooltip_text = article.headline

    if len(display_headline) > truncate_length:
        display_headline = f"{display_headline[:truncate_length-3]}..."

    return f'{display_headline} | href={article.link} tooltip="{tooltip_text}"'

def main():
    scraper = BNDScraper()
    articles = scraper.get_headlines()

    if not articles:
        print("No headlines available. Please check the website manually. | color=red")
        print("---")
        print("Open BND.com | href=https://www.bnd.com")
        return

    print("BND")
    print("---")

    for article in articles:
        print(format_menu_item(article))

    print("---")
    print(f"Last Updated: {datetime.now().strftime('%I:%M %p')} | color=gray")
    print("Open BND.com | href=https://www.bnd.com")
    print("Refresh | refresh=true")

if __name__ == "__main__":
    main()