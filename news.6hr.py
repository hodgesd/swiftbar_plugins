#!/usr/bin/env PYTHONIOENCODING=UTF-8 python3
import requests
from bs4 import BeautifulSoup


def fetch_news(url):
    try:
        response = requests.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        articles = []
        for li in soup.select('ul.article-listing li'):
            article = {}
            headline = li.find('h1').find('a')
            time_tag = li.find('time')
            article['title'] = headline.get_text(strip=True)
            article['url'] = headline['href']
            article['date'] = time_tag.get_text(strip=True) if time_tag else 'No date'
            article['summary'] = li.find('div', class_='summary').find('p').get_text(strip=True)
            articles.append(article)

        return articles
    except requests.RequestException as e:
        return [{'title': 'Error fetching news', 'url': '#', 'summary': str(e)}]


def main():
    url = 'https://www.scott.af.mil/News/'
    articles = fetch_news(url)
    print('􀤦')
    print("---")
    for article in articles:
        print(f"{article['date']}: {article['title']} | href={article['url']}")



if __name__ == "__main__":
    main()
