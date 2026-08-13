# 闲鱼网页版搜索结果卡片选择器（goofish.com）。
# 真实 DOM 使用带哈希的 CSS Modules 类名（如 feeds-item-wrap--rGdH_KoF），
# 因此用“模块名前缀 + --”做稳定匹配；data-spm 是阿里系稳定埋点属性。
# 平台改版时只需调整本文件。
RESULT_CARD = "div[data-spm='searchFeedList'] > a[href*='item']"
RESULT_CARD_FALLBACK = "a[href*='/item?id=']"
TITLE = "[class*='main-title--']"
PRICE = "[class*='price-wrap--']"
IMAGE = "img[class*='feeds-image--']"
SELLER = "[class*='seller-text--']"
LOCATION = "[class*='seller-text--']"

# 商品详情页（实测：描述 span[class*='desc--']，主图 img.ant-image-img）
DETAIL_DESC = "span[class*='desc--']"
DETAIL_IMAGE = "img[class*='ant-image-img']"

# 商品详情页卖家区块（实测：链接 /personal?userId=，昵称 item-user-info-nick--，信用 credit-container--）
DETAIL_SELLER_LINK = "a[href*='/personal?userId=']"
DETAIL_SELLER_NICK = "[class*='item-user-info-nick--']"
DETAIL_CREDIT_LABEL = "[class*='credit-container--']"
