from pathlib import Path

import pytest

from goodprice.crawler.parser import extract_id, parse_price, parse_search_html

FIXTURE = Path(__file__).parent / "fixtures" / "xianyu_search.html"


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
    assert first.url == "https://www.goofish.com/item?id=1001"
    assert first.image_urls == ["https://img.goofish.com/1001.jpg"]
    assert first.seller == "小明"
    assert first.location == "杭州"
    second = items[1]
    assert second.external_id == "1002"
    assert second.seller is None
