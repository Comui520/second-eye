"""爬虫共用的网络常量。

UA 与闲鱼域名曾分别在 xianyu.py / login.py / parser.py 各写一份，这里集中定义，
避免升级浏览器指纹或换域名时漏改。
"""

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

GOOFISH_ORIGIN = "https://www.goofish.com"
GOOFISH_HOME = f"{GOOFISH_ORIGIN}/"

# Playwright 超时/等待（毫秒），原先散落在 xianyu.py 各处。
NAV_TIMEOUT_MS = 45000
SELECTOR_TIMEOUT_MS = 30000
RENDER_SETTLE_MS = 3000
SELLER_SETTLE_MS = 5000
SELLER_DETAIL_SETTLE_MS = 4000
