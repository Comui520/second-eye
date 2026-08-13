import re
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from goodprice.crawler import selectors as sel
from goodprice.crawler.base import ListingData, ListingDetail, SellerData

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


def parse_detail_html(html: str) -> ListingDetail:
    soup = BeautifulSoup(html, "html.parser")
    variants: list[dict] = []
    range_el = soup.select_one(sel.DETAIL_PRICE_RANGE)
    if range_el:
        m = re.search(r"(\d+(?:\.\d+)?)\s*[-~至]\s*(\d+(?:\.\d+)?)", range_el.get_text(" ", strip=True))
        if m:
            variants = [
                {"name": "最低价", "price": float(m.group(1))},
                {"name": "最高价", "price": float(m.group(2))},
            ]
    desc = ""
    desc_el = soup.select_one(sel.DETAIL_DESC)
    if desc_el:
        desc = desc_el.get_text(" ", strip=True)
    if not desc:
        for el in soup.select("[class*='desc--']"):
            text = el.get_text(" ", strip=True)
            if len(text) > len(desc) and "想要" not in text and not text.startswith("¥"):
                desc = text
    images: list[str] = []
    for img in soup.select(sel.DETAIL_IMAGE):
        src = img.get("src")
        if src and is_product_image(src):
            url = _absolute(src)
            if url not in images:
                images.append(url)
    seller_link = soup.select_one(sel.DETAIL_SELLER_LINK)
    seller_uid = extract_user_id(seller_link.get("href")) if seller_link else None
    seller_name, positive_rate, sold_count, _ = _parse_seller_block(seller_link)
    nick_el = soup.select_one(sel.DETAIL_SELLER_NICK)
    if nick_el:
        seller_name = nick_el.get_text(strip=True) or seller_name
    credit_el = soup.select_one(sel.DETAIL_CREDIT_LABEL)
    credit_label = credit_el.get_text(strip=True) if credit_el else None
    return ListingDetail(
        description=desc[:2000],
        image_urls=images[:8],
        variants=variants,
        seller_uid=seller_uid,
        seller_name=seller_name,
        credit_label=credit_label,
        positive_rate=positive_rate,
        sold_count=sold_count,
    )


def extract_user_id(href: str) -> Optional[str]:
    match = re.search(r"userId=([^&]+)", href or "")
    return match.group(1) if match else None


def is_product_image(url: str) -> bool:
    """真实商品图判定：alicdn 产品图路径含 bao/uploaded；占位图/图标（tps-）一律排除。"""
    return "bao/uploaded" in (url or "")


def _parse_seller_block(seller_link):
    if seller_link is None:
        return None, None, None, None
    block_text = seller_link.get_text(" ", strip=True)
    first_line = seller_link.get_text("\n", strip=True).splitlines()
    name = first_line[0] if first_line else None
    positive_rate = None
    sold_count = None
    m = re.search(r"好评率\s*([\d.]+)%", block_text)
    if m:
        positive_rate = float(m.group(1)) / 100
    m = re.search(r"卖出\s*(\d+)\s*件", block_text)
    if m:
        sold_count = int(m.group(1))
    return name, positive_rate, sold_count, block_text


_TAG_NAMES = ("沟通愉快", "收货快", "回复快", "下单爽快", "描述真实", "发货快")


def parse_seller_html(html: str, seller_uid: str) -> SellerData:
    soup = BeautifulSoup(html, "html.parser")
    body = soup.get_text("\n", strip=True)
    positive = None
    total = None
    m = re.search(r"好评\s*(\d+)", body)
    if m:
        positive = int(m.group(1))
    m = re.search(r"信用及评价\s*(\d+)", body)
    if m:
        total = int(m.group(1))
    tags = []
    for tag in _TAG_NAMES:
        m = re.search(re.escape(tag) + r"\s*(\d+)", body)
        if m:
            tags.append(f"{tag} {m.group(1)}")
    return SellerData(
        seller_uid=seller_uid, positive_count=positive, total_count=total, tags=tags
    )
