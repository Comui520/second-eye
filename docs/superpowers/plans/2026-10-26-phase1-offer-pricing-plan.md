# 阶段一：规格与可信价格 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 使预算、排名和提醒只使用匹配规格的可靠价格，支持待询价与独立人工报价，安全迁移旧库。

**Architecture:** 纯函数裁决 + 不可变 DTO + 短事务服务；保持原调度入口可用，后续用相同阶段函数接新协调器。

**Tech Stack:** Python >=3.11、Pydantic、SQLAlchemy/SQLite、Playwright、pytest。

**Spec:** `D:/good-price/docs/superpowers/specs/2026-10-26-variant-price-media-design.md` §3–4、§7–9。

## Global Constraints

继承 `2026-10-26-upgrade-roadmap.md` 全部约束。金额用分整数与 Decimal 转换；人工报价默认 7 天；未知价不排名；最低品相分 >0 且未核验时进入待核验。

## Review Focus

- 测试 import 副作用触碰生产库（P1.1）；“256GB”匹配“128GB”或同名组合合并（P1.2/P1.3）。
- ORM 迁移隐式丢数据、外键孤儿（P1.2）；人工价/页面价切换伪降价（P1.3/P1.4）；部分表单清空报价（P1.5）。

### P1.1：测试隔离与无副作用应用工厂

**Files:** 修改 `goodprice/main.py`、`goodprice/config.py`、`goodprice/__main__.py`、`tests/conftest.py`、`tests/test_main.py`；新增 `tests/test_import_isolation.py`、`scripts/benchmark_pipeline.py`。

**Interfaces:** `build_app(settings: Settings | None = None, session_factory=None, with_scheduler: bool = True) -> FastAPI` 保持；新增 `create_app() -> FastAPI` 作为部署工厂；uvicorn 改 factory 调用。日志路径从显式 settings/data_root 获取，import 不 mkdir/init_db/backfill。

- [ ] 写 `test_import_does_not_initialize_db_or_write_logs`：子进程在临时 cwd 导入 main/入口，不出现项目 data 写入、连接或调度启动；对 Path/engine 禁止生产路径访问。写 `test_explicit_app_uses_only_temporary_store`，断言 app 数据库、日志、通知与 scheduler 隔离。
- [ ] 运行 `uv run --locked pytest -q tests/test_import_isolation.py`，应因当前 import-time app 初始化而失败；保存失败证据，不运行真实库。
- [ ] 实现 lazy factory，fixture 在收集前指定临时资源目录，所有模型与平台测试使用 fake/MockTransport；新增基准脚本只构建显式测试工厂，拒绝非临时数据库与真实外部 URL。
- [ ] 运行上述用例和 `tests/test_main.py`，应通过；运行基准合成 30 件商品，保存原架构耗时/SQL/调用次数到测试生成目录，不提交机器私有报告。
- [ ] 独立审查无副作用与入口兼容后，提交 `test: isolate app startup and establish pipeline baseline`。

### P1.2：规范化规格与幂等迁移

**Files:** 新增 `goodprice/offers/__init__.py`、`goodprice/offers/types.py`、`goodprice/offers/matching.py`、`goodprice/migrations.py`、`tests/test_offer_migrations.py`、`tests/test_offer_identity.py`；修改 `models.py`、`db.py`、`services/task_service.py`、`web/routes.py` 的 TaskCreate/任务表单。

**Interfaces:**
- `stable_offer_key(platform_sku_id: str | None, attributes: dict[str, str]) -> str | None`；歧义不强制造 key。
- `match_variant(target: str, attributes: dict[str, str], title: str) -> Literal['matched','mismatched','uncertain']`。
- PriceClaim：`source: str`、`kind: str`、`text: str`、`amount_cents: int | None`、`min_cents/max_cents: int | None`、`scope_key: str | None`、`observed_at: datetime`、`evidence_id: str`。
- OfferCandidate：`key: str`、`attributes: dict[str,str]`、`availability: str`、`claims: tuple[PriceClaim,...]`；提供默认单件 offer 而非区间 SKU。
- 新增 ListingOffer、OfferObservation、ManualQuote、OfferEvaluation、SchemaVersion 表；WatchTask 新字段 target_variant/notify_pending_price/version，Notification 增可空 offer_id/event_key；旧字段保留。

- [ ] 写迁移测试：v0.1.1 合成库迁移两次，原任务/通知/快照逐项不变；旧最低/最高价不变成真实 offer，标记 legacy_unverified；默认新字段不触发重发；删除父记录无新孤儿。
- [ ] 写身份/匹配测试：不同显示顺序 key 不变、相同属性但不同原站 SKU 不合并、256GB 不匹配 128GB、目标含否定约束且无证据时 uncertain。
- [ ] 运行 `pytest tests/test_offer_migrations.py tests/test_offer_identity.py -q`，确认新接口缺失导致失败。
- [ ] 实现模型/DTO/带版本事务迁移，识别旧安装须发生在初始化改变表结构之前；备份恢复只在临时库演练。任务 API/表单统一白名单，版本随业务条件改变递增。
- [ ] 全量验证旧模型/DB/TaskService/API 测试及新测试，范围默认取 Spec；审查后提交 `feat: add versioned offer and price evidence storage`。

### P1.3：价格、来源、报价和新鲜度裁决

**Files:** 新增 `goodprice/offers/pricing.py`、`goodprice/offers/service.py`、`tests/test_offer_pricing.py`、`tests/test_manual_quotes.py`。

**Interfaces:**
- `parse_money_cents(raw: str) -> int`，有限非负人民币金额，精度超过分拒绝，不浮点猜价。
- QuoteInput：`offer_key: str, amount_cents: int, confirmed_at: datetime, expires_at: datetime, evidence_fingerprint: str, revoked: bool=False`。
- PriceDecision：`state: str, amount_cents: int | None, source: str | None, evidence_ids: tuple[str,...], reason: str, fresh_until: datetime | None`。
- `decide_price(offer: OfferCandidate, quote: QuoteInput | None, now: datetime, ttl_seconds: int) -> PriceDecision`。
- `is_comparable_drop(previous: PriceDecision, current: PriceDecision, same_offer: bool) -> bool`。
- `OfferService(session_factory)` 的 `record_observation(listing_id: int, offer: OfferCandidate) -> int`、`confirm_quote(offer_id: int, quote: QuoteInput) -> int`、`revoke_quote(quote_id: int) -> None`，校验与保存同事务。

- [ ] 写最小断言：`parse_money_cents('1500.00') == 150000`；参数化拒绝 NaN/Infinity/负数/不支持币种/小数三位。
  最小红绿测试：
  ```python
  def test_money_uses_integer_cents():
      assert parse_money_cents("1500.00") == 150000
  ```
- [ ] 写确价、range、询价、冲突、未知、作用范围不明、SKU 售罄/未知、人工价优先/变化暂停/七天过期、一个调度间隔证据过期测试；页面1500/报价1400/预算1450须使用有效报价，不用页面价。
- [ ] 写 `test_source_switch_is_not_price_drop`、`test_refresh_failure_does_not_extend_freshness`、`test_revoke_keeps_original_observation`，运行新用例确认红。
- [ ] 实现优先级和新鲜度纯函数，服务在 commit 前校验合并值；指纹排除纯采集时间变化、包含价格/规格/作用范围。保存证据与人工来源分离。
- [ ] 运行新用例与原 satisfaction/crawl tests；审查后提交 `feat: resolve trustworthy prices and manual quotes`。

### P1.4：真实 SKU 采集和价格正确性接入

**Files:** 新增 `goodprice/crawler/offers.py`、`goodprice/services/offer_pipeline.py`、`tests/test_offer_parser.py`、`tests/test_offer_pipeline.py`；修改 crawler/base.py、parser.py、xianyu.py、services/crawl_service.py、satisfaction.py；新增脱敏/合成 fixtures。

**Interfaces:** `parse_offer_candidates(html: str) -> list[OfferCandidate]`；`OfferPipeline(session_factory)` 的 `evaluate_listing(listing_id: int, task_id: int, candidates: list[OfferCandidate], now: datetime) -> list[int]` 返回已保存评估 ID；网络/模型通过注入客户端执行，不把 session 包在请求周围。

- [ ] fixture 包含配件99/128GB1200/256GB1500；测试目标256GB预算1400时不入选、1600时入选、搜索标价99低于任务下限仍抓详情。文本私聊价保留待确认且没有性价比调用。
- [ ] 写任务 fetch_detail=False、20组合截断、30详情预算、空/异常详情不误判售罄、旧商品刷新、重新上架状态、最低品相分未核验不放行等失败用例。
- [ ] 运行 `pytest tests/test_offer_parser.py tests/test_offer_pipeline.py -q` 确认红。
- [ ] 实现页面范围限定解析和有界只读选项交互，确切来源才确价，移走详情前价格过滤；普通单件生成默认 offer。未适配页面保留 unknown，不将平台状态猜成支持格式。
- [ ] 将已有串行运行接到相同 offer 阶段函数，保存与通知以 offer 事件引用；旧 listing 标价仅兼容展示，去重不按采集时间每轮生成新事件；有限搜索未见改为疑似未见。
- [ ] 无样本时写明实验性边界，运行 parser/crawler/crawl/notification 全部测试及新回归；审查后提交 `feat: evaluate listings using matching offer prices`。

### P1.5：规格/价格交互、通知与阶段验收

**Files:** 新增 `goodprice/web/offer_routes.py`、`tests/test_offer_api.py`；修改 routes.py 注册 router，商品卡片/详情/任务表单/通知文案及原 API 测试。

**Interfaces:** GET `/api/listings/{id}/offers`，POST `/offers/{id}/quotes`（金额、到期、确认原文），POST `/quotes/{id}/revoke`；响应包含 price_state/effective_price/source/fresh_until/evidence，不复用旧 price 的“确认价”含义。

- [ ] 写缺失/错误报价返回422且无写入；不存在 offer 返回404；报价不会被抓取覆盖；待询价开关关闭不外发；同规格相同事件成功渠道不重发、来源切换不叫降价。
- [ ] 明确兼容测试：旧API仍有原页面标价 `price`，新 effective_price/source 不隐式取旧值；父摘要基于当前任务已匹配offer确定生成，多个offer时显示范围/数量而不伪称统一确价。
- [ ] 运行新增 API/通知测试确认红；实现模板与路由，状态 unknown/pending 不生成本批最优，不把空价渲染成0；未保存秘密不回显。
- [ ] 执行 `uv lock --check`、`uv run --locked ruff check .`、`uv run --locked pytest -q --cov --cov-fail-under=80`；临时库验证迁移/恢复、api 与页面渲染。
- [ ] 阶段独立审查，按指纹/金额/旧库/提示误导检查；红绿回归修复后提交 `feat: expose offer evidence and inquiry workflow`，勾选总计划阶段一记录。
