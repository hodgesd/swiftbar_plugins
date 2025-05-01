#!/Users/hodgesd/PycharmProjects/swiftbar_plugins/.venv/bin/python3.12
# <bitbar.title>AAFES Deals</bitbar.title>
# <bitbar.author>hodgesd</bitbar.author>
# <bitbar.author.github>hodgesd</bitbar.author.github>
# <bitbar.desc>Show current deals from AAFES Exchange.com</bitbar.desc>
# <bitbar.dependencies>python</bitbar.dependencies>
# <bitbar.version>1.0</bitbar.version>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideLastUpdated>true</swiftbar.hideLastUpdated>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>


import requests
from bs4 import BeautifulSoup

headers = {'User-Agent': 'Mozilla/5.0'}


def parse_price(price):
    return price.split(':')[1].strip()


def scrape_aafes_deals():
    url = 'https://www.shopmyexchange.com/s?Dy=1&Nty=1&Ntt=dotd'
    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.content, 'html.parser')

    # Example of scraping. Adjust based on actual page structure since BeautifulSoup does not execute JavaScript.
    # This is a placeholder for where you would parse the document.
    sales_items = soup.find_all(class_='aafes-thumbnail-item col-xs-12')

    # Assuming 'sales_items' is a list of elements you're interested in,
    # you would loop through and extract data similar to the JavaScript version.
    for item in sales_items:
        item_name = item.find(class_='aafes-item-name').get_text(strip=True)
        item_link = item.find('a')['href']

        # Check if element exists before calling get_text
        item_sale_price_element = item.find(class_='aafes-price-sale')
        if item_sale_price_element:
            raw_item_sale_price = item_sale_price_element.get_text(strip=True)

            item_sale_price = parse_price(raw_item_sale_price)
        else:
            item_sale_price = 'N/A'

        item_discount_element = item.find(class_='aafes-price-saved')
        item_discount = item_discount_element.get_text(strip=True) if item_discount_element else 'N/A'

        # Ensure the link is complete if it's relative
        if not item_link.startswith('http'):
            item_link = 'https://www.shopmyexchange.com' + item_link

        print(f"-{item_sale_price} [-{item_discount}]{item_name} |href={item_link}")


if __name__ == '__main__':
    scrape_aafes_deals()
