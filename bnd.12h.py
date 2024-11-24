#!/Users/hodgesd/PycharmProjects/swiftbar_plugins/.venv/bin/python3.12

# <xbar.title>Belleville News Dispatch Headlines</xbar.title>
# <xbar.version>v1.1</xbar.version>
# <xbar.author>MajorDouble</xbar.author>
# <xbar.author.github>YourGitHub</xbar.github>
# <xbar.desc>Bnd.com headlines with enhanced timeout handling and debugging.</xbar.desc>
# <xbar.dependencies>python,requests,bs4</xbar.dependencies>

import requests
from requests.adapters import HTTPAdapter, Retry
from bs4 import BeautifulSoup
import re
from datetime import datetime
import time

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

def main():
    articles = get_headlines()

    if not articles:
        print("No headlines available. Please check the website manually. | color=red")
        print("---")
        print("Open BND.com | href=https://www.bnd.com")
        return

    for article in articles:
        # Add category if available
        display_headline = article['headline']
        if article['category']:
            display_headline = f"{article['category']}: {display_headline}"

        # Truncate for display with full headline as tooltip
        if len(display_headline) > 60:
            tooltip_text = display_headline
            display_headline = f"{display_headline[:57]}..."
            print(f"{display_headline} | href={article['link']} tooltip={tooltip_text}")
        else:
            print(f"{display_headline} | href={article['link']}")

    print("---")
    print(f"Last Updated: {datetime.now().strftime('%I:%M %p')} | color=gray")
    print("Open BND.com | href=https://www.bnd.com")
    print("Refresh | refresh=true")

if __name__ == "__main__":
    main()