from dataclasses import dataclass
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit

FIELDS = {"task_id", "show", "sort", "price_state", "loaded"}


@dataclass(frozen=True)
class ListContext:
    task_id: int | None = None
    show: str = "active"
    sort: str = "satisfaction"
    price_state: str = "all"
    loaded: int = 100

    @classmethod
    def from_query(cls, query):
        def single(name, default):
            values = query.getlist(name) if hasattr(query, "getlist") else [query.get(name, default)]
            return str(values[0])[:100] if len(values) == 1 else default
        raw_id = single("task_id", "")
        task_id = int(raw_id) if raw_id.isascii() and raw_id.isdigit() and 0 < int(raw_id) <= 2**31-1 else None
        raw_loaded = single("loaded", "100")
        loaded = min(1000, max(100, int(raw_loaded))) if raw_loaded.isascii() and raw_loaded.isdigit() else 100
        def choice(name, options, default):
            raw = single(name, default)
            return raw if raw in options else default
        return cls(task_id, choice("show", {"active", "gone", "blocked", "all", "unseen"}, "active"),
                   choice("sort", {"satisfaction", "price_asc", "price_desc", "newest"}, "satisfaction"),
                   choice("price_state", {"all", "exact", "pending"}, "all"), loaded)

    def values(self):
        values = {"show": self.show, "sort": self.sort}
        if self.task_id:
            values = {"task_id": str(self.task_id), **values}
        if self.price_state != "all":
            values["price_state"] = self.price_state
        if self.loaded != 100:
            values["loaded"] = str(self.loaded)
        return values

    def url(self, path="/listings"):
        return path + "?" + urlencode(self.values())


def safe_return_to(raw, fallback="/listings"):
    if not raw or len(raw) > 2048:
        return fallback
    decoded = raw
    for _ in range(3):
        if any(ord(c) < 32 for c in decoded) or "\\" in decoded:
            return fallback
        decoded = unquote(decoded)
    try:
        parts = urlsplit(raw)
    except ValueError:
        return fallback
    if parts.scheme or parts.netloc or parts.path != "/listings" or parts.fragment:
        return fallback
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    if any(k not in FIELDS for k, v in pairs) or len({k for k, v in pairs}) != len(pairs):
        return fallback
    context = ListContext.from_query(dict(pairs))
    canonical = context.values()
    return "/listings" + ("?" + urlencode({k: canonical[k] for k, v in pairs if k in canonical}) if pairs else "")
