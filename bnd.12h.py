#!/Users/hodgesd/PycharmProjects/swiftbar_plugins/.venv/bin/python3.12

# <xbar.title>Belleville News Dispatch Headlines</xbar.title>
# <xbar.version>v1.1</xbar.version>
# <xbar.author>MajorDouble</xbar.author>
# <xbar.author.github>YourGitHub</xbar.github>
# <xbar.desc>Bnd.com headlines with enhanced timeout handling and debugging.</xbar.desc>
# <xbar.dependencies>python,requests,bs4</xbar.dependencies>

import re
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter, Retry

print("BND")
print("---")

def clean_text(text):
    """Clean up text by removing extra whitespace and newlines"""
    return re.sub(r'\s+', ' ', text).strip()

def get_headlines():
    try:
        # Headers to mimic a browser
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

        # Enable retries and longer timeouts
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

        # Add a small delay before request
        time.sleep(1)

        # Fetch webpage
        response = session.get("https://www.bnd.com", timeout=20, verify=True)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        articles = []
        seen_links = set()  # To track unique articles

        # First, get articles from the grid section
        content_area = soup.find('section', class_='grid')
        if content_area:
            for article in content_area.find_all('article', recursive=True):
                if not article.find_parent(class_='partner-digest-group'):
                    headline_elem = article.find('h3')
                    if headline_elem and headline_elem.find('a'):
                        link = headline_elem.find('a').get('href', '')
                        if not link.startswith('http'):
                            link = f"https://www.bnd.com{link}"

                        # Skip duplicates
                        if link in seen_links:
                            continue
                        seen_links.add(link)

                        headline = clean_text(headline_elem.get_text())

                        # Get category if available
                        category = ''
                        kicker = article.find(class_='kicker')
                        if kicker:
                            category = clean_text(kicker.get_text())

                        articles.append({
                            'headline': headline,
                            'link': link,
                            'category': category
                        })

        # Then, get articles from the Latest section
        latest_section = soup.find('div', attrs={'data-tb-region': 'latest'})
        if latest_section:
            for article in latest_section.find_all('div', class_='package'):
                headline_elem = article.find('h3')
                if headline_elem and headline_elem.find('a'):
                    link = headline_elem.find('a').get('href', '')
                    if not link.startswith('http'):
                        link = f"https://www.bnd.com{link}"

                    # Skip duplicates
                    if link in seen_links:
                        continue
                    seen_links.add(link)

                    headline = clean_text(headline_elem.get_text())

                    # Latest news items might not have a category, but we can label them
                    category = 'Latest News'

                    articles.append({
                        'headline': headline,
                        'link': link,
                        'category': category
                    })

        return articles

    except requests.Timeout:
        print("Timeout while fetching headlines. Retrying in 5 minutes... | color=red")
        return []
    except requests.RequestException as e:
        print(f"Request error: {str(e)} | color=red")
        return []
    except Exception as e:
        print(f"An unexpected error occurred: {str(e)} | color=red")
        return []

def category_abbreviation(category):
    """Convert long category names to abbreviated versions"""
    abbreviations = {
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
    return abbreviations.get(category, f'[{category}]')

def main():
    articles = get_headlines()

    if not articles:
        print("No headlines available. Please check the website manually. | color=red")
        print("---")
        print("Open BND.com | href=https://www.bnd.com")
        return

    for article in articles:
        # Store original headline
        original_headline = article['headline']

        # Create display version with abbreviated category
        display_headline = original_headline
        if article['category']:
            display_headline = f"{category_abbreviation(article['category'])} {original_headline}"

        # Create tooltip with full category if available
        tooltip_text = original_headline
        if article['category']:
            tooltip_text = f"{article['category']}: {original_headline}"

        # Truncate display headline if needed
        if len(display_headline) > 75:
            display_headline = f"{display_headline[:72]}..."
            print(f'{display_headline} | href={article["link"]} tooltip="{tooltip_text}"')
        else:
            print(f'{display_headline} | href={article["link"]} tooltip="{tooltip_text}"')

    print("---")
    print(f"Last Updated: {datetime.now().strftime('%I:%M %p')} | color=gray")
    print("Open BND.com | href=https://www.bnd.com")
    print("Refresh | refresh=true")

if __name__ == "__main__":
    main()