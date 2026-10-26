import hashlib
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from goodprice.crawler.constants import GOOFISH_ORIGIN
from goodprice.media.types import MediaCandidate


def extract_media_candidates(html: str) -> list[MediaCandidate]:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one("[data-item-detail], main, [class*='detail-container']")
    if root is None:
        return []
    container = root.select_one("[data-item-media], [class*='item-slider'], [class*='main-media']")
    nodes = (container.select("img, video") if container else
             root.select("img.ant-image-img, [data-item-video]"))
    result = []
    seen = set()
    for node in nodes:
        if node.find_parent(["aside", "nav"]) or node.find_parent(attrs={"data-recommend": True}):
            continue
        kind = "video" if node.name == "video" else "image"
        source = node.select_one("source[src]") if kind == "video" else None
        raw = node.get("src") or (source.get("src") if source else "") or node.get("data-src")
        if not raw:
            continue
        url = urljoin(GOOFISH_ORIGIN, raw)
        sku = node.get("data-sku-id")
        scope = f"sku:{sku}" if sku else None
        identity = (kind, url, scope)
        if identity in seen:
            continue
        seen.add(identity)
        parsed = urlsplit(url)
        supported = parsed.scheme in {"http", "https"} and not parsed.path.lower().endswith((".m3u8", ".mpd")) and not node.get("data-drm")
        result.append(MediaCandidate(
            hashlib.sha256(repr(identity).encode()).hexdigest()[:32], kind, url, scope,
            f"main-media:{len(result)}", node.get("poster"),
            "pending" if supported else "unsupported"))
    return result
