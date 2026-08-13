from pathlib import Path

import pytest

from goodprice.crawler.parser import extract_id, parse_detail_html, parse_price, parse_search_html

FIXTURE = Path(__file__).parent / "fixtures" / "xianyu_search.html"
DETAIL_FIXTURE = Path(__file__).parent / "fixtures" / "xianyu_detail.html"


def test_parse_price():
    assert parse_price("¥2999.00") == 2999.0
    assert parse_price(" 450 ") == 450.0
    with pytest.raises(ValueError):
        parse_price("面议")


def test_extract_id():
    assert extract_id("https://www.goofish.com/item?id=1001") == "1001"
    assert extract_id("https://www.goofish.com/item/abc123?x=1") == "abc123"
    assert extract_id("https://www.goofish.com/other") is None


def test_parse_search_html():
    items = parse_search_html(FIXTURE.read_text(encoding="utf-8"))
    assert len(items) == 2
    first = items[0]
    assert first.external_id == "1001"
    assert first.title == "iPhone 13 128G 蓝色"
    assert first.price == 2999.0
    assert first.url == "https://www.goofish.com/item?id=1001&categoryId=1"
    assert first.image_urls == ["https://img.alicdn.com/1001.jpg"]
    assert first.seller == "杭州"
    assert first.location == "杭州"
    second = items[1]
    assert second.external_id == "1002"
    assert second.price == 450.0
    assert second.seller is None


def test_parse_detail_html():
    detail = parse_detail_html(DETAIL_FIXTURE.read_text(encoding="utf-8"))
    assert "屏幕完好" in detail.description
    assert "带原装盒" in detail.description
    assert detail.image_urls == ["https://img.alicdn.com/d1.jpg", "https://img.alicdn.com/d2.jpg"]
    assert detail.seller_uid == "2672367114"
    assert detail.seller_name == "饼住呼吸"
    assert detail.credit_label == "卖家信用极好"
    assert detail.positive_rate == 100.0
    assert detail.sold_count == 264


def test_extract_user_id():
    from goodprice.crawler.parser import extract_user_id

    assert extract_user_id("https://www.goofish.com/personal?userId=2672367114") == "2672367114"
    assert extract_user_id("https://x/other") is None
