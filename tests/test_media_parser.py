from goodprice.crawler.parser import parse_detail_html
from goodprice.media.parser import extract_media_candidates


def test_only_main_media_with_scope_and_stable_order():
    html = '''<main><div data-item-media><img src="https://cdn.example/one.jpg">
      <video data-sku-id="s1" src="https://cdn.example/movie.mp4" poster="/poster.jpg"></video>
      <img src="https://cdn.example/one.jpg"></div>
      <aside><video src="https://evil.example/ad.mp4"></video></aside></main>
      <div data-recommend><img src="https://evil.example/recommend.jpg"></div>'''
    items = extract_media_candidates(html)
    assert [x.kind for x in items] == ["image", "video"]
    assert items[0].offer_key is None
    assert items[1].offer_key == "sku:s1"
    assert parse_detail_html(html).media == items


def test_unsupported_video_kept_as_unavailable_not_image():
    items = extract_media_candidates('<main><div data-item-media><video src="blob:abc"></video><video src="https://cdn.example/a.m3u8"></video></div></main>')
    assert len(items) == 2
    assert all(x.status == "unsupported" for x in items)


def test_known_detail_images_without_media_container_exclude_recommendations():
    items = extract_media_candidates('''<main><img class="ant-image-img" src="https://a/bao/uploaded/one.jpg">
      <aside><img class="ant-image-img" src="https://a/bao/uploaded/ad.jpg"></aside></main>''')
    assert len(items) == 1
