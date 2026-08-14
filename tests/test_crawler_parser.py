from pathlib import Path

import pytest

from goodprice.crawler.parser import (
    extract_id,
    parse_detail_html,
    parse_price,
    parse_search_html,
    parse_seller_html,
)

FIXTURE = Path(__file__).parent / "fixtures" / "xianyu_search.html"
DETAIL_FIXTURE = Path(__file__).parent / "fixtures" / "xianyu_detail.html"
VARIANT_FIXTURE = Path(__file__).parent / "fixtures" / "xianyu_detail_variants.html"
SELLER_FIXTURE = Path(__file__).parent / "fixtures" / "xianyu_seller.html"


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
    assert first.image_urls == ["https://img.alicdn.com/bao/uploaded/1001.jpg"]
    assert first.seller == "杭州"
    assert first.location == "杭州"
    second = items[1]
    assert second.external_id == "1002"
    assert second.price == 450.0
    assert second.seller is None


def test_parse_search_html_drops_placeholder_images():
    html = """
    <div data-spm="searchFeedList">
      <a href="/item?id=1"><span class="main-title--xx">真图商品</span><div class="price-wrap--xx">¥100</div><img class="feeds-image--xx" src="https://img.alicdn.com/bao/uploaded/x.jpg"></a>
      <a href="/item?id=2"><span class="main-title--xx">占位图商品</span><div class="price-wrap--xx">¥200</div><img class="feeds-image--xx" src="https://img.alicdn.com/imgextra/i4/xx-2-tps-2-2.png"></a>
    </div>
    """
    items = parse_search_html(html)
    assert items[0].image_urls == ["https://img.alicdn.com/bao/uploaded/x.jpg"]
    assert items[1].image_urls == []


def test_parse_detail_html_main_image_first():
    html = """
    <html><body>
      <img class="ant-image-img" src="https://img.alicdn.com/bao/uploaded/album1.jpg">
      <img class="ant-image-img" src="https://img.alicdn.com/bao/uploaded/main1-xy_item.jpg">
      <img class="ant-image-img" src="https://img.alicdn.com/imgextra/i4/tps-2-2.png">
      <img class="ant-image-img" src="https://img.alicdn.com/bao/uploaded/main2-xy_item.jpg">
    </body></html>
    """
    detail = parse_detail_html(html)
    assert detail.image_urls == [
        "https://img.alicdn.com/bao/uploaded/main1-xy_item.jpg",
        "https://img.alicdn.com/bao/uploaded/main2-xy_item.jpg",
        "https://img.alicdn.com/bao/uploaded/album1.jpg",
    ]


def test_parse_detail_html():
    detail = parse_detail_html(DETAIL_FIXTURE.read_text(encoding="utf-8"))
    assert "屏幕完好" in detail.description
    assert "带原装盒" in detail.description
    assert detail.image_urls == [
        "https://img.alicdn.com/bao/uploaded/d1.jpg",
        "https://img.alicdn.com/bao/uploaded/d2.jpg",
    ]
    assert detail.seller_uid == "2672367114"
    assert detail.seller_name == "饼住呼吸"
    assert detail.credit_label == "卖家信用极好"
    assert detail.positive_rate == 1.0  # 好评率 100% 存为小数
    assert detail.sold_count == 264
    assert detail.variants == []


def test_parse_detail_html_price_range_as_variants():
    detail = parse_detail_html(VARIANT_FIXTURE.read_text(encoding="utf-8"))
    assert detail.variants == [
        {"name": "最低价", "price": 850.0},
        {"name": "最高价", "price": 1299.0},
    ]


def test_extract_user_id():
    from goodprice.crawler.parser import extract_user_id

    assert extract_user_id("https://www.goofish.com/personal?userId=2672367114") == "2672367114"
    assert extract_user_id("https://x/other") is None


def test_is_product_image_filters_placeholder():
    from goodprice.crawler.parser import is_product_image

    assert is_product_image("https://img.alicdn.com/bao/uploaded/i2/x.jpg") is True
    assert is_product_image("https://img.alicdn.com/imgextra/i4/xxx-2-tps-2-2.png") is False
    assert is_product_image("https://img.alicdn.com/imgextra/i1/xxx-tps-480-144.png") is False


def test_parse_seller_html():
    data = parse_seller_html(SELLER_FIXTURE.read_text(encoding="utf-8"), "2672367114")
    assert data.seller_uid == "2672367114"
    assert data.positive_count == 133
    assert data.total_count == 194
    assert any("沟通愉快 13" in t for t in data.tags)
