"""Parser tests for microcenter_deals_uv.6h.py.

Run:  uv run --with pytest --with feedparser --with curl_cffi pytest tests/
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location("deals", ROOT / "microcenter_deals_uv.6h.py")
deals = importlib.util.module_from_spec(spec)
sys.modules["deals"] = deals  # dataclasses resolve string annotations via sys.modules
spec.loader.exec_module(deals)


def make(title: str) -> "deals.Deal":
    return deals.Deal(
        title=title, thread_id="1", thread_url="", store_url="", published=None,
        expired=False, price=None, source="",
    )


THREAD_PAGE = (
    '<script>dataLayer.push({"finalPrice":"1,299.99","listPrice":"1,799.99",'
    '"subCategory":"Video Cards & GPUs","primaryCategory":"Computers",'
    '"category":"Computers|Video Cards & GPUs","isExpiredDeal":"No"});</script>'
)


def test_parse_thread_html_fields():
    info = deals.parse_thread_html(THREAD_PAGE)
    assert info["price"] == 1299.99
    assert info["list_price"] == 1799.99
    assert info["sub_category"] == "Video Cards & GPUs"
    assert info["primary_category"] == "Computers"
    assert info["categories"] == ["Computers", "Video Cards & GPUs"]
    assert info["expired"] is False


def test_parse_thread_html_nulls_and_expired():
    page = '"finalPrice":null,"listPrice":null,"subCategory":null,"isExpiredDeal":"Yes"'
    info = deals.parse_thread_html(page)
    assert info["price"] is None and info["list_price"] is None
    assert info["sub_category"] == ""
    assert info["expired"] is True


def test_parse_thread_html_empty_page():
    assert deals.parse_thread_html("<html></html>") == {}


@pytest.mark.parametrize(
    "title, expected",
    [
        ("Intel Core i5-14600K CPU for $149.99 + Free Shipping", "Intel Core i5-14600K CPU"),
        ("Micro Center: $79 - 1TB NVMe SSD", "1TB NVMe SSD"),
        ("Extra $50 off DeWalt 20V Drill Kit", "Extra $50 off DeWalt 20V Drill Kit"),
        ("$50 off ASUS Monitor", "$50 off ASUS Monitor"),
    ],
)
def test_strip_price(title, expected):
    assert deals.strip_price(title) == expected


@pytest.mark.parametrize(
    "title",
    [
        "DeWalt 20V MAX Brushless Drill Kit",
        "DeWalt 20V Impact Drivers",
        "DeWalt Circular Saws",
    ],
)
def test_power_tool_titles(title):
    assert make(title).is_power_tool


@pytest.mark.parametrize(
    "title",
    [
        "DeWalt Kitchen Faucet",
        "DeWalt Lighter Set",
        "Sawyer Water Filter",
        "DeWalt 100-pc Drill Bit Set",
        "DeWalt Safety Glasses",
        "DeWalt Earbuds",
    ],
)
def test_non_power_tool_titles(title):
    assert not make(title).is_power_tool
