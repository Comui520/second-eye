import re
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from goodprice.crawler import selectors as sel
from goodprice.crawler.base import ListingData

BASE_URL = "https://www.goofish.com"
_PRICE_RE = re.compile(r"(\d+(?:\.\d+)?)")


def parse_price(text: str) -> float:
    match = _PRICE_RE.search(text or "")
    if not match:
        raise ValueError(f"无法解析价格: {text!r}")
    return float(match.group(1))


def extract_id(href: str) -> Optional[str]:
    match = re.search(r"[?&]id=([^&]+)", href)
    if match:
        return match.group(1)
    match = re.search(r"/item/([^/?#]+)", href)
    if match:
        return match.group(1)
    return None


def _absolute(url: str) -> str:
    return urljoin(BASE_URL, url)


def parse_search_html(html: str, card_selector: str = sel.RESULT_CARD) -> list[ListingData]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ListingData] = []
    seen: set[str] = set()
    for card in soup.select(card_selector):
        if card.name == "a" and card.get("href"):
            href = card.get("href")
        else:
            link_el = card.select_one("a[href]")
            href = link_el.get("href") if link_el else None
        if not href:
            continue
        external_id = extract_id(href)
        if not external_id or external_id in seen:
            continue
        title_el = card.select_one(sel.TITLE)
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue
        price_el = card.select_one(sel.PRICE)
        try:
            price = parse_price(price_el.get_text() if price_el else "")
        except ValueError:
            continue
        img_el = card.select_one(sel.IMAGE)
        image_urls = [img_el.get("src")] if img_el and img_el.get("src") else []
        seller_el = card.select_one(sel.SELLER)
        location_el = card.select_one(sel.LOCATION)
        items.append(
            ListingData(
                external_id=external_id,
                title=title,
                price=price,
                url=_absolute(href),
                image_urls=[_absolute(u) for u in image_urls],
                seller=seller_el.get_text(strip=True) if seller_el else None,
                location=location_el.get_text(strip=True) if location_el else None,
            )
        )
        seen.add(external_id)
    return items
