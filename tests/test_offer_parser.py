from goodprice.crawler.offers import parse_offer_candidates


def test_reads_actual_sku_combinations_not_price_range():
    html = '''<main data-item-detail>
      <div data-sku-id="part" data-attributes='{"型号":"配件"}' data-price="99"></div>
      <div data-sku-id="128" data-attributes='{"型号":"iPhone 15","容量":"128GB"}' data-price="1200"></div>
      <div data-sku-id="256" data-attributes='{"型号":"iPhone 15","容量":"256GB"}' data-price="1500"></div>
      </main><aside><div data-sku-id="ad" data-price="1"></div></aside>'''
    offers = parse_offer_candidates(html)
    assert [o.key for o in offers] == ["sku:part", "sku:128", "sku:256"]
    assert offers[2].claims[0].amount_cents == 150000


def test_only_range_is_not_exact_sku():
    offers = parse_offer_candidates('<main data-item-detail><span data-item-price>850 - 1299</span></main>')
    assert len(offers) == 1
    assert offers[0].claims[0].kind == "range_or_from"
    assert offers[0].claims[0].amount_cents is None


def test_inquiry_and_unknown_do_not_invent_prices():
    inquiry = parse_offer_candidates('''<main data-item-detail><p data-item-description>所有标价非售价，私聊咨询价格</p>
      <span data-item-price>1</span></main>''')[0]
    assert any(c.kind == "inquiry" for c in inquiry.claims)
    assert parse_offer_candidates("<body>推荐商品99元</body>")[0].claims[0].kind == "unknown"


def test_collision_and_truncation_are_not_claimed_complete():
    offers = parse_offer_candidates('<main data-item-detail>' +
        ''.join(f'<div data-sku-id="{i}" data-price="100"></div>' for i in range(21)) + '</main>')
    assert len(offers) == 20
    assert all(any(c.kind == "unknown" and "不完整" in c.text for c in o.claims) for o in offers)
    duplicate = parse_offer_candidates('''<main data-item-detail>
      <div data-sku-id="1" data-price="100"></div><div data-sku-id="1" data-price="200"></div></main>''')
    assert len(duplicate) == 1
    assert duplicate[0].claims[-1].kind == "conflict"


def test_jsonld_product_used_and_global_disclaimer_not_ignored():
    html = '''<main data-item-detail><script type="application/ld+json">{
      "@type":"Product","description":"标价非售价，咨询价格",
      "offers":{"@type":"Offer","price":"1500","priceCurrency":"CNY"}}
      </script></main>'''
    offer = parse_offer_candidates(html)[0]
    assert any(c.kind == "inquiry" for c in offer.claims)
