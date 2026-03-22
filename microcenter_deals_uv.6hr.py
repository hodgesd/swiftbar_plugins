#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "aiohttp>=3.8.0",
#     "beautifulsoup4>=4.9.0",
#     "curl_cffi>=0.7.0",
#     "pydantic>=2.0.0",
# ]
# ///

# <swiftbar.title>Microcenter Top Deals</swiftbar.title>
# <swiftbar.version>v1.1</swiftbar.version>
# <swiftbar.author>Derrick Hodges</swiftbar.author>
# <swiftbar.author.github>hodgesd</swiftbar.author.github>
# <swiftbar.desc>Displays current deals from Microcenter's Top Deals and Lowes Kobalt</swiftbar.desc>
# <swiftbar.dependencies>uv, beautifulsoup4, aiohttp, curl_cffi, pydantic</swiftbar.dependencies>

import asyncio
import datetime
import json
import re
from typing import Optional, List, cast

import aiohttp
from aiohttp import ClientTimeout
from bs4 import BeautifulSoup
from pydantic import BaseModel, HttpUrl, Field

# Constants
MICROCENTER_DEALS_URL = "https://www.microcenter.com/search/search_results.aspx?fq=Micro+Center+Deals:Top+Deals"
LOWES_KOBALT_URL = "https://www.lowes.com/search?searchTerm=kobalt&refinement=982555943,982555939,982555940,4294965883,2,982555941&int_cmp=%3A%3ATools%3A%3APOPCAT_OFFERS_Kobalt_Hybrid"
BING_KOBALT_URL = "https://www.bing.com/shop?q=kobalt+tools+deals+lowes&count=40"
REQUEST_TIMEOUT = 15
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


class MicrocenterDeal(BaseModel):
    name: str
    sku: str
    current_price: float = Field(gt=0)
    original_price: Optional[float] = None
    savings: Optional[float] = None
    url: HttpUrl
    brand: Optional[str] = None
    in_store_only: bool = False

    @property
    def discount_percentage(self) -> Optional[float]:
        """Calculate discount percentage if original price exists."""
        if self.original_price and self.original_price > 0:
            return (
                (self.original_price - self.current_price) / self.original_price
            ) * 100
        return None

    @property
    def color(self) -> str:
        """Determine color based on discount percentage."""
        discount = self.discount_percentage
        if discount is None:
            return ""
        if discount >= 20:
            return "green"
        elif discount >= 10:
            return "blue"
        return ""


class LowesKobaltItem(BaseModel):
    """Kobalt product from Lowes search results."""

    name: str
    item_id: str
    current_price: float = Field(gt=0)
    original_price: Optional[float] = None
    url: HttpUrl


async def fetch_page(url: str) -> Optional[str]:
    """Fetch page content with error handling."""
    timeout = ClientTimeout(total=REQUEST_TIMEOUT)
    try:
        async with aiohttp.ClientSession(
            timeout=timeout, connector=aiohttp.TCPConnector(ssl=False)
        ) as session:
            async with session.get(url, headers=HEADERS) as response:
                response.raise_for_status()
                return await response.text()
    except Exception as e:
        print(f"💻\n---\n⚠️ Error fetching Microcenter deals: {e}")
        return None


def fetch_kobalt_via_bing() -> List[LowesKobaltItem]:
    """Fetch Kobalt products via Bing Shopping and map them to Lowe's product URLs."""
    from curl_cffi import requests as creq

    try:
        r = creq.get(BING_KOBALT_URL, impersonate="chrome", timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            return []
    except Exception:
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    lowes_offer_urls = extract_lowes_offer_urls(r.text)
    items: List[LowesKobaltItem] = []
    seen_names: set[str] = set()

    for card in soup.select(".br-item"):
        offer_attr = card.get("data-offerid") or card.get("data-offerId") or ""
        if isinstance(offer_attr, list):
            offer_attr = offer_attr[0] if offer_attr else ""
        offer_id = str(offer_attr).strip()

        seller_el = card.select_one(".br-sellerName")
        seller = seller_el.get_text().strip() if seller_el else ""
        if "lowe" not in seller.lower():
            continue

        title_el = card.select_one("[title]")
        title_attr = title_el.get("title", "") if title_el else ""
        if isinstance(title_attr, list):
            title_attr = title_attr[0] if title_attr else ""
        name = str(title_attr).strip()
        if not name:
            name_div = card.select_one(".br-title")
            name = name_div.get_text().strip() if name_div else ""
        if not name or len(name) < 10 or name in seen_names:
            continue
        if "kobalt" not in name.lower():
            continue
        seen_names.add(name)

        price_el = card.select_one(".pd-price, .br-price")
        current_price = parse_price(price_el.get_text()) if price_el else None
        if not current_price or current_price <= 0:
            continue

        was_el = card.select_one(".br-oPrice")
        original_price = parse_price(was_el.get_text()) if was_el else None

        if not original_price or original_price <= current_price:
            continue

        url = lowes_offer_urls.get(offer_id, "")

        if not url:
            link_el = card.select_one("a.br-titlelink")
            if link_el:
                href_attr = link_el.get("href", "")
                if isinstance(href_attr, list):
                    href_attr = href_attr[0] if href_attr else ""
                href = str(href_attr)
                if href:
                    url = (
                        href
                        if href.startswith("http")
                        else f"https://www.bing.com{href}"
                    )

        if not url:
            continue

        try:
            items.append(
                LowesKobaltItem(
                    name=name[:100],
                    item_id=name[:20],
                    current_price=current_price,
                    original_price=original_price,
                    url=cast(HttpUrl, url),
                )
            )
        except Exception:
            continue

    return items


def extract_lowes_offer_urls(html: str) -> dict[str, str]:
    """Extract Lowe's product page URLs keyed by offer ID from Bing's embedded data."""
    offer_urls: dict[str, str] = {}

    for raw_blob in re.findall(r'"CustomData":"(\{.*?\})"', html):
        try:
            decoded_blob = raw_blob.encode("utf-8").decode("unicode_escape")
            payload = json.loads(decoded_blob)
        except Exception:
            continue

        offer_id = str(payload.get("GlobalOfferId", "")).strip()
        page_url = str(payload.get("PageUrl", "")).strip()

        if not offer_id or "lowes.com" not in page_url.lower():
            continue

        offer_urls[offer_id] = page_url

    return offer_urls


def parse_price(price_text: str) -> Optional[float]:
    """Extract numeric price from text like '$199.99' or '1,799.99'."""
    if not price_text:
        return None
    cleaned = re.sub(r"[^\d.]", "", price_text.strip())
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_deals(html: str) -> List[MicrocenterDeal]:
    """Parse deals from HTML content using data attributes."""
    soup = BeautifulSoup(html, "html.parser")
    deals = []

    product_links = soup.find_all(
        "a", {"data-id": True, "data-price": True, "data-name": True}
    )

    seen_skus = set()

    for link in product_links:
        try:
            sku = link.get("data-id", "")

            if sku in seen_skus:
                continue
            seen_skus.add(sku)

            name = (
                link.get("data-name", "").replace("&quot;", '"').replace("&amp;", "&")
            )
            current_price = parse_price(link.get("data-price", ""))
            brand = link.get("data-brand", "")
            url = link.get("href", "")

            if url and "auth/signin" in url:
                url = f"https://www.microcenter.com/product/{sku}/"
            elif url and not url.startswith("http"):
                url = "https://www.microcenter.com" + url

            if not (name and url and current_price and sku):
                continue

            parent = link.find_parent("li")

            original_price = None
            savings = None

            if parent:
                save_text = parent.find(string=re.compile(r"Save\s*\$", re.I))
                if save_text:
                    save_match = re.search(
                        r"\$[\d,]+\.?\d*", save_text.parent.get_text()
                    )
                    if save_match:
                        savings = parse_price(save_match.group())
                        original_price = current_price + savings

                orig_text = parent.find(string=re.compile(r"Original\s+price", re.I))
                if orig_text and not original_price:
                    orig_match = re.search(
                        r"\$[\d,]+\.?\d*", orig_text.parent.get_text()
                    )
                    if orig_match:
                        original_price = parse_price(orig_match.group())
                        if original_price and current_price:
                            savings = original_price - current_price

                in_store_only = bool(
                    parent.find(
                        string=re.compile(r"(BUY IN STORE|in[- ]?store only)", re.I)
                    )
                )
            else:
                in_store_only = False

            deal = MicrocenterDeal(
                name=name[:100],
                sku=sku,
                current_price=current_price,
                original_price=original_price,
                savings=savings,
                url=url,
                brand=brand if brand else None,
                in_store_only=in_store_only,
            )
            deals.append(deal)

        except Exception as e:
            continue

    return deals


def format_output(
    deals: List[MicrocenterDeal],
    kobalt_items: Optional[List[LowesKobaltItem]] = None,
):
    """Format deals for SwiftBar output."""
    kobalt_items = kobalt_items or []

    print("💻")
    print("---")

    if not deals:
        print("Microcenter")
        print("--No deals found")
        print(f"--Last checked: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("---")
    else:
        sorted_deals = sorted(
            deals,
            key=lambda d: (
                d.discount_percentage if d.discount_percentage else 0,
                d.savings if d.savings else 0,
            ),
            reverse=True,
        )

        print("Microcenter")
        print(f"--Last updated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(
            f"--Microcenter Top Deals ({len(deals)} items) | href={MICROCENTER_DEALS_URL}"
        )
        print("---")

        for deal in sorted_deals:
            discount_text = ""
            color_attr = ""

            if deal.savings:
                discount_text = f" [-${deal.savings:.0f}]"
                if deal.color:
                    color_attr = f" color={deal.color}"

            store_indicator = " 🏪" if deal.in_store_only else ""

            display_name = deal.name[:60] + "..." if len(deal.name) > 60 else deal.name

            print(
                f"--${deal.current_price:.2f}{discount_text} {display_name}{store_indicator} | href={deal.url}{color_attr}"
            )

            if deal.original_price:
                print(f"----Original: ${deal.original_price:.2f}")
                print(f"----Sale: ${deal.current_price:.2f}")
                if deal.discount_percentage:
                    print(f"----Discount: {deal.discount_percentage:.0f}% off")

            if deal.brand:
                print(f"----Brand: {deal.brand}")

            print(f"----SKU: {deal.sku}")

            if deal.in_store_only:
                print("----⚠️ In-store only")

    # Kobalt submenu
    print("---")
    print("Kobalt")
    print(f"--Last updated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    browse_text = (
        f"--Browse Kobalt at Lowes ({len(kobalt_items)} items)"
        if kobalt_items
        else "--Browse Kobalt at Lowes"
    )
    print(f"{browse_text} | href={LOWES_KOBALT_URL}")
    if kobalt_items:
        sorted_kobalt = sorted(kobalt_items, key=lambda k: k.current_price)
        for item in sorted_kobalt:
            discount_text = ""
            if item.original_price and item.original_price > item.current_price:
                discount_text = f" [-${item.original_price - item.current_price:.0f}]"
            display_name = item.name[:60] + "..." if len(item.name) > 60 else item.name
            print(
                f"--${item.current_price:.2f}{discount_text} {display_name} | href={item.url}"
            )

    # Footer
    print("---")
    print("Refresh | refresh=true")


async def main():
    """Main execution function."""
    mc_html = await fetch_page(MICROCENTER_DEALS_URL)

    deals = parse_deals(mc_html) if mc_html else []

    # curl_cffi is synchronous; run in executor to avoid blocking the event loop
    loop = asyncio.get_running_loop()
    kobalt_items = await loop.run_in_executor(None, fetch_kobalt_via_bing)

    format_output(deals, kobalt_items)


if __name__ == "__main__":
    asyncio.run(main())
