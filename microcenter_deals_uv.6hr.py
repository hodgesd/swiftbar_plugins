#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "aiohttp>=3.8.0",
#     "beautifulsoup4>=4.9.0",
#     "pydantic>=2.0.0",
# ]
# ///

# <swiftbar.title>Microcenter Top Deals</swiftbar.title>
# <swiftbar.version>v1.1</swiftbar.version>
# <swiftbar.author>Derrick Hodges</swiftbar.author>
# <swiftbar.author.github>hodgesd</swiftbar.author.github>
# <swiftbar.desc>Displays current deals from Microcenter's Top Deals page</swiftbar.desc>
# <swiftbar.dependencies>uv, beautifulsoup4, aiohttp, pydantic</swiftbar.dependencies>

import asyncio
import datetime
import re
from typing import Optional, List

import aiohttp
from aiohttp import ClientTimeout
from bs4 import BeautifulSoup
from pydantic import BaseModel, HttpUrl, Field

# Constants
MICROCENTER_DEALS_URL = "https://www.microcenter.com/search/search_results.aspx?fq=Micro+Center+Deals:Top+Deals"
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
            return ((self.original_price - self.current_price) / self.original_price) * 100
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


async def fetch_page(url: str) -> Optional[str]:
    """Fetch page content with error handling."""
    timeout = ClientTimeout(total=REQUEST_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout, connector=aiohttp.TCPConnector(ssl=False)) as session:
            async with session.get(url, headers=HEADERS) as response:
                response.raise_for_status()
                return await response.text()
    except Exception as e:
        print(f"💻\n---\n⚠️ Error fetching Microcenter deals: {e}")
        return None


def parse_price(price_text: str) -> Optional[float]:
    """Extract numeric price from text like '$199.99' or '1,799.99'."""
    if not price_text:
        return None
    # Remove currency symbols, commas, and whitespace
    cleaned = re.sub(r'[^\d.]', '', price_text.strip())
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_deals(html: str) -> List[MicrocenterDeal]:
    """Parse deals from HTML content using data attributes."""
    soup = BeautifulSoup(html, 'html.parser')
    deals = []
    
    # Find all product links with data attributes (much more reliable)
    product_links = soup.find_all('a', {'data-id': True, 'data-price': True, 'data-name': True})
    
    # Track SKUs we've already added to avoid duplicates
    seen_skus = set()
    
    for link in product_links:
        try:
            # Extract data from attributes
            sku = link.get('data-id', '')
            
            # Skip duplicates
            if sku in seen_skus:
                continue
            seen_skus.add(sku)
            
            name = link.get('data-name', '').replace('&quot;', '"').replace('&amp;', '&')
            current_price = parse_price(link.get('data-price', ''))
            brand = link.get('data-brand', '')
            url = link.get('href', '')
            
            # Clean up URL - avoid signin redirects
            if url and 'auth/signin' in url:
                # Extract the product ID and construct direct URL
                url = f'https://www.microcenter.com/product/{sku}/'
            elif url and not url.startswith('http'):
                url = 'https://www.microcenter.com' + url
            
            # Skip if missing essential data
            if not (name and url and current_price and sku):
                continue
            
            # Find the parent container to check for additional info
            parent = link.find_parent('li')
            
            # Look for original price and savings
            original_price = None
            savings = None
            
            if parent:
                # Check for price savings text
                save_text = parent.find(string=re.compile(r'Save\s*\$', re.I))
                if save_text:
                    save_match = re.search(r'\$[\d,]+\.?\d*', save_text.parent.get_text())
                    if save_match:
                        savings = parse_price(save_match.group())
                        original_price = current_price + savings
                
                # Also check for "Original price" text
                orig_text = parent.find(string=re.compile(r'Original\s+price', re.I))
                if orig_text and not original_price:
                    orig_match = re.search(r'\$[\d,]+\.?\d*', orig_text.parent.get_text())
                    if orig_match:
                        original_price = parse_price(orig_match.group())
                        if original_price and current_price:
                            savings = original_price - current_price
                
                # Check if in-store only
                in_store_only = bool(parent.find(string=re.compile(r'(BUY IN STORE|in[- ]?store only)', re.I)))
            else:
                in_store_only = False
            
            # Create deal object
            deal = MicrocenterDeal(
                name=name[:100],  # Truncate long names
                sku=sku,
                current_price=current_price,
                original_price=original_price,
                savings=savings,
                url=url,
                brand=brand if brand else None,
                in_store_only=in_store_only
            )
            deals.append(deal)
        
        except Exception as e:
            # Skip items that can't be parsed
            continue
    
    return deals


def format_output(deals: List[MicrocenterDeal]):
    """Format deals for SwiftBar output."""
    # Menu bar icon
    print("💻")
    print("---")
    
    if not deals:
        print("No deals found")
        print(f"Last checked: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("---")
        print("Refresh | refresh=true")
        return
    
    # Sort by highest discount percentage first, then by savings amount
    sorted_deals = sorted(
        deals,
        key=lambda d: (
            d.discount_percentage if d.discount_percentage else 0,
            d.savings if d.savings else 0
        ),
        reverse=True
    )
    
    # Header
    print(f"Last updated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Microcenter Top Deals ({len(deals)} items) | href={MICROCENTER_DEALS_URL}")
    print("---")
    
    # Display each deal
    for deal in sorted_deals:
        # Main line with price and discount
        discount_text = ""
        color_attr = ""
        
        if deal.savings:
            discount_text = f" [-${deal.savings:.0f}]"
            if deal.color:
                color_attr = f" color={deal.color}"
        
        store_indicator = " 🏪" if deal.in_store_only else ""
        
        # Truncate name for menu bar
        display_name = deal.name[:60] + '...' if len(deal.name) > 60 else deal.name
        
        print(f"${deal.current_price:.2f}{discount_text} {display_name}{store_indicator} | href={deal.url}{color_attr}")
        
        # Submenu items with details
        if deal.original_price:
            print(f"--Original: ${deal.original_price:.2f}")
            print(f"--Sale: ${deal.current_price:.2f}")
            if deal.discount_percentage:
                print(f"--Discount: {deal.discount_percentage:.0f}% off")
        
        if deal.brand:
            print(f"--Brand: {deal.brand}")
        
        print(f"--SKU: {deal.sku}")
        
        if deal.in_store_only:
            print("--⚠️ In-store only")
    
    # Footer
    print("---")
    print("Refresh | refresh=true")


async def main():
    """Main execution function."""
    html = await fetch_page(MICROCENTER_DEALS_URL)
    
    if not html:
        return
    
    deals = parse_deals(html)
    format_output(deals)


if __name__ == "__main__":
    asyncio.run(main())
