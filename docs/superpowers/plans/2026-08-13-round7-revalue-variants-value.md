# 第七轮：更新重评估 + 多规格价格 + 横向性价比 + 下架隐藏 + 配置修复

## 目标

- 修复 GLM 配置串线（文本模型 Base URL 停在 DeepSeek、模型名大小写错误），并让 API 真实报错透出到界面。
- 每轮抓取结束、逐项分析完后，把本批通过筛选的商品交给 LLM 横向对比，输出每个商品的性价比分（1-10）并标出「本批最优」。
- 商品价格变化触发存量重评估；满意度严格提高才再次推送，通知标明"价格更新重推：旧价→新价"。
- 详情页解析价格区间（闲鱼网页版不暴露逐规格价格，只有区间），任一价≤最高价即通过；命中列表/通知显示价格区间。
- 连续 3 轮未见标记"已下架"，默认隐藏、可筛选查看；再次出现自动恢复在售并触发一次重评。
- 前端做低成本交互流畅性改进；视频解析本轮不做。

## 关键实现

### 配置修复
- 库内设置：`llm_base_url=https://open.bigmodel.cn/api/paas/v4`、`llm_model=glm-4.7-flash`、`vision_model=glm-4.6v-flash`（小写）；API Key 不动。
- 设置页补模型名小写提示。

### LLM 客户端（analysis/llm.py）
- 非 2xx 响应：抛 `httpx.HTTPStatusError`，消息含状态码 + 响应体前 300 字符（保留 `analyze_condition` 的图片降级分支依赖的异常类型）。
- 429 限流：自动重试最多 3 次，间隔可注入（默认 5s，测试置 0）。
- 新增 `analyze_batch_value(items)`：输入一批 {external_id, title, price, condition_score, defects, seller_risk}，输出 {"scores": {external_id: 1-10}, "best": external_id, "reasons": {...}}。

### 数据模型（models.py + db.py）
- `Listing` 新列：`status`(默认 active)、`missed_count`(默认 0)、`variants`(JSON 默认 [])、`value_score`、`value_batch_at`、`best_of_batch`(默认 False)、`last_notified_satisfaction`。
- `migrate_schema` 幂等补齐这些列。

### 解析器（crawler/parser.py + base.py）
- `ListingDetail` 增加 `variants`。
- 详情页价格区间 `div[class*='price--'][class*='windows--']` 文本如 "850 - 1299" → `[{"name": "最低价", "price": 850.0}, {"name": "最高价", "price": 1299.0}]`；单价格无区间则留空。
- 落 fixture 并写解析测试。

### 评分公式（services/satisfaction.py）
- 视觉开：需求40 + 品相30 + 性价比20 + 卖家10；视觉关：需求50 + 性价比30 + 卖家20。
- `requirement_match`/`value_score` 缺失按半值计；品相缺失按 0；卖家低/中/高 = 满/半/0。

### 流水线（services/crawl_service.py）
- 批量性价比只依赖文字，**优先使用文本 LLM**（未配置时退回视觉模型），避免占用视觉模型配额/限流。
- 新品：详情 → 需求 → 品相 → 门槛 → 卖家 → 入批；批性价比分析完成后统一通知（文案含性价比分与本批最优标记）。
- 存量：搜索价变化才触发重评（用库内描述/图，不重抓详情页）；重评后满意度严格高于 `last_notified_satisfaction` 才重推。
- 下架：本轮未出现的该任务商品 `missed_count+1`，≥3 → `status=gone`；再次出现恢复 active 并走重评路径。
- 批量性价比：仅对通过需求 + 品相门槛的商品；上限 30；失败则 value_score 置空、记录日志、不拦截通知。

### 页面（web/routes.py + templates）
- 命中列表 show 筛选：在售/已下架/全部/已拉黑；卡片显示 本批最优/已下架 徽标与价格区间。
- 操作反馈：删除/拉黑后 URL 参数触发 toast；删除加确认。
- 设置页：模型名小写提示。

## 测试计划
- 单元：LLM 错误透出、429 重试、批量性价比解析；新列默认值与迁移；详情页区间解析 fixture；四维评分与缺值；重评/重推判定；下架计数与恢复；批量性价比失败降级。
- 接口：`/listings?show=gone` 与 `/api/listings` 筛选。
- 回归：全量 pytest 通过。

## 假设与默认
- 性价比只横向对比本批；「本批最优」取本批第一名。
- 存量重评不重抓详情页；变体（区间）只在首次详情抓取时记录，网页端不提供逐规格价格。
- 实测 `glm-4.6v-flash` 高峰期限流（429），代码已加 3 次自动重试；如持续失败可在设置切回 `glm-4.1v-thinking-flash`。
- 下架是软状态，保留全部历史，仅默认隐藏。
- 视频解析、通知摘要合并不在本轮范围。
