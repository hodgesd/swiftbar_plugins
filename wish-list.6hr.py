#!/Users/hodgesd/PycharmProjects/swiftbar_plugins/.venv/bin/python3.12
import datetime
from typing import Optional, List

import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel, HttpUrl, Field, field_validator


class Product(BaseModel):
    name: str
    url: HttpUrl
    msrp: float = Field(gt=0)
    selector: str
    variant_id: Optional[str] = None

    @field_validator('msrp')
    @classmethod
    def msrp_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError('MSRP must be positive')
        return v


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36"
}

PRODUCTS: List[Product] = [
    Product(
        name="GORUCK Rucker",
        url="https://www.goruck.com/products/rucker?variant=44172656279652",
        msrp=255.00,
        selector='span.product__price[data-product-price]',
        variant_id="44172656279652"
    ),
    # Add more products here
]


def get_price(product: Product) -> Optional[float]:
    try:
        response = requests.get(str(product.url), headers=HEADERS)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        selector = product.selector
        if product.variant_id:
            selector += f'[bss-b2b-variant-id="{product.variant_id}"]'

        price_element = soup.select_one(selector)
        if price_element:
            price_text = price_element.text.strip()
            return float(price_text.replace('$', '').replace(',', ''))
        else:
            print(f"----Price element not found for {product.name} | color=red")
            return None
    except requests.RequestException as e:
        print(f"----Error fetching price for {product.name}: {e} | color=red")
        return None


def calculate_price_change(current_price: float, msrp: float) -> tuple:
    if current_price < msrp:
        change_log = f" 📉 ${current_price - msrp:.2f} ({-((current_price - msrp) / msrp * 100):.2f}%) | color=green"
        color = "green"
    elif current_price > msrp:
        change_log = f" 📈 ${current_price - msrp:.2f} ({((current_price - msrp) / msrp * 100):.2f}%) | color=red"
        color = "red"
    else:
        change_log = "⚖️"
        color = "blue"
    return change_log, color


def main():
    print("$ | color=blue")
    print("---")
    print(f"Last update: {datetime.datetime.now():%Y-%m-%d %H:%M}")

    for product in PRODUCTS:
        current_price = get_price(product)
        if current_price is not None:
            change_log, color = calculate_price_change(current_price, product.msrp)
            print(f"{product.name} {change_log} | color={color}")
            print(f"--Current Price: ${current_price:.2f} | color=blue")
            print(f"--MSRP: ${product.msrp:.2f} | color=blue")
            print(f"--Open {product.name} Page | href={product.url}")
            print("-----")


if __name__ == "__main__":
    main()
