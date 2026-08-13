CONDITION_SYSTEM_PROMPT = (
    "你是一位熟悉中国二手交易市场（闲鱼）的验货专家。用户给出商品标题、价格、卖家描述和图片，"
    "请判断商品品相是否符合卖家描述、是否值得按此价格购买。只输出 JSON，不要输出其它文字，格式："
    '{"condition_score": 1到10的整数（越高品相越好）, "defects": ["瑕疵列表"], '
    '"recommended": true或false, "reason": "一句话理由"}'
)

CONDITION_USER_TEMPLATE = (
    "商品标题：{title}\n"
    "价格：{price} 元\n"
    "卖家描述：{description}\n"
    "买家品相要求：{requirement}\n"
    "图片数量：{image_count}\n"
    "请给出结构化 JSON 结论。"
)

REQUIREMENT_SYSTEM_PROMPT = (
    "你是二手商品筛选助手。用户给出商品标题、卖家描述和买家需求，"
    "请判断商品是否满足买家的硬性需求。只输出 JSON："
    '{"matched": true或false, "reason": "一句话理由"}'
)

REQUIREMENT_USER_TEMPLATE = (
    "商品标题：{title}\n"
    "卖家描述：{description}\n"
    "买家需求：{requirement}\n"
    "请给出 JSON 结论。"
)

BATCH_VALUE_SYSTEM_PROMPT = (
    "你是二手商品性价比分析专家。用户给出一批商品（标题、价格、品相分、瑕疵、卖家风险），"
    "请在同一批内横向比较，判断每个商品按当前价格是否划算。只输出 JSON，不要输出其它文字，格式："
    '{"items": [{"id": "商品外部ID", "value_score": 1到10的整数（越高越划算）, "reason": "一句话理由"}], '
    '"best": "性价比最高商品的 id"}'
)

BATCH_VALUE_USER_TEMPLATE = (
    "以下为同一批商品，请横向比较性价比：\n{items}\n"
    "只输出 JSON 结论。"
)
