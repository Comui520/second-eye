# 阶段三：有界流水线、资源推荐与交互 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 让抓取、媒体、模型不再互相串行阻塞，在可配置的资源边界内可靠恢复，并修复全站导航和设置交互。

**Architecture:** 持久化状态与短事务单写入协调器，有限线程池处理I/O，Playwright由所属线程独占，FFmpeg为受限子进程；模板保留简洁风格。

**Tech Stack:** Python >=3.11、APScheduler、线程池、SQLAlchemy/SQLite、Playwright、HTMX、psutil、pytest。

**Spec:** `D:/good-price/docs/superpowers/specs/2026-10-26-variant-price-media-design.md` §6–9；前置为阶段一、二。

## Global Constraints

继承总计划；任务同一时刻一个run，最多合并一次后续到期信号。默认active runs=2，抓取/模型/媒体worker各1，FFmpeg线程1；可调上限为4/2/8/2/4。平台间隔默认10秒(5–300)，每账号并行导航1；API RPM默认10(1–60)。配置只影响新派发工作，降低并发先排空，设备建议不自动提高配额。

## Review Focus

- 修改/删除/暂停后的迟到结果和重启通知重复（P3.1–P3.3）。
- 达到工作项上限时游标丢失、单任务饿死其他任务（P3.1/P3.2）。
- 容器无quota/嵌套限额/低内存误报强硬件（P3.4）。
- 查询丢失、加载更多重复、未提交设置分类被清空（P3.5/P3.6）。
- 性能数据改善来自减少分析覆盖而非架构提速（P3.7对比同数据/同输入预算）。

### P3.1：持久化工作状态、租约与版本

**Files:** 新增 `goodprice/execution/{__init__,types,store}.py`、`tests/test_execution_store.py`；修改models.py/migrations.py、services/task_service.py。

**Interfaces:** Stage 枚举 search/detail/parse/media/analyze/aggregate/notify；RunToken：run_id/task_id/task_version/lease_id；WorkResult：token/stage/input_fingerprint/output 的不可变DTO；新增 TaskRun/WorkItem/NotificationEvent/NotificationDelivery ORM。
`ExecutionStore(session_factory)` 提供 `submit(task_id: int, kind: str, target_id: int | None = None) -> int | None`、`claim(stage: Stage, now: datetime) -> WorkItemDTO | None`、`complete(result: WorkResult, now: datetime) -> bool`、`recover(now: datetime) -> int`、`invalidate_task(task_id: int) -> None`；只传ID/DTO，不向worker泄漏session。

所有DTO在execution/types.py：RunToken各ID为int、lease_id为str；WorkItemDTO含 `id: int, token: RunToken, stage: Stage, input_fingerprint: str, payload: dict, attempt: int, deadline_at: datetime`；WorkResult.output为dict。Run/Work状态与通知状态分开，禁止拿NotificationDelivery的sent冒充Run完成。

- [x] 写同任务去重、运行中多次触发只补一次、全局200/每任务60背压、恢复游标、暂停删除和版本变更拒绝迟到complete、租约只被一个worker领取等失败测试。
- [x] 写跨轮观察过期先刷新，实质内容不变复用已有模型结果；观察变化使相关下游失效，不把未知当否定。
- [x] 运行 `pytest tests/test_execution_store.py -q` 确认红；实现带版本迁移、条件更新和短事务状态机，notification独立租约而非依赖已结束run。
- [x] 回归迁移/报价/任务服务；独立审查后提交 `feat: persist bounded task execution state`。

### P3.2：资源通道、调度与取消恢复

**Files:** 新增 `goodprice/execution/limits.py`、`workers.py`、`coordinator.py`、`tests/test_execution_limits.py`、`tests/test_coordinator.py`；修改scheduler.py、main.py、crawler/xianyu.py、services/crawl_service.py、旧queue测试。

**Interfaces:**
- `ResourceLimits.from_settings(settings: Settings) -> ResourceLimits`（全部并发、RPM、间隔、工作项/run预算），含服务端范围验证。
- `RateGate.reserve(key: str, now: float) -> float` 返回最早允许时间；`WorkerPools.dispatch(stage: Stage, token: RunToken, payload: dict) -> Future[WorkResult]`。
- `Coordinator(store: ExecutionStore, limits: ResourceLimits, workers: WorkerPools, clock)` 提供 `start()/stop(timeout: float)/submit(task_id: int)/submit_reanalysis(listing_id: int)/pause(task_id: int)/update_limits(limits: ResourceLimits)`。
- `BrowserWorker` 所在线程负责 playwright/browser/context 创建/使用/关闭；平台节点执行包含SKU只读交互的账号级节流；先前offer/media阶段函数保持相同输入输出。

- [x] 用受控Future/假时钟写慢模型期间第二任务抓取可推进、active=1时明确串行、同账号导航峰值1、不同资源峰值<=设置、429 Retry-After/重试计RPM、等待不占其他槽、轮转公平测试。
- [x] 写15分钟墙钟/详情30/HTTP60预算延后、关机恢复只补未完成或过期步骤、取消/改任务后模型迟到不提交、降低并发先排空、高压力暂停派发不丢工作。
- [x] 写scheduler修改任务interval后触发器更新、禁用队列任务不再执行、manual重新分析走同一资源池的失败用例，运行确认红。
- [x] 实现单进程协调器与按阶段线程所有权；去掉整任务完成后固定300秒等待，保留平台速率/风险退避；APScheduler仅提交信号与not-before。
- [x] 确保业务网络调用不在写事务中；分阶段替换原TaskQueue而不双消费，app生命周期可靠关闭浏览器/线程/子进程，debug/reload不启两套scheduler。
- [x] 并发/任务/队列/爬虫全部回归并独立审查后提交 `feat: run crawling and analysis through bounded stages`。

### P3.3：独立通知投递与可信批次聚合

**Files:** 新增 `goodprice/execution/delivery.py`、`tests/test_delivery_lifecycle.py`；修改offer_pipeline、crawl_service的聚合/通知路径、通知历史API/模型。

**Interfaces:** `DeliveryService(store, notifiers)` 的 `deliver(event_id: int, now: datetime) -> str`、`retry(delivery_id: int, now: datetime) -> str`；返回 pending/sent/failed/uncertain/awaiting_revalidation/superseded/cancelled。通知发送前核验task版本与价格新鲜度。

- [x] 写run完成后合法pending可发送、run超时后迟到计算不能生成事件、暂停/删除/改条件撤销待发送、证据过期延后、不重复成功渠道、崩溃unknown结果uncertain不得自动重发等失败用例。
- [x] 写人工重试也重新核验；确价恢复但实质证据未变复用原业务事件；同一批次只比较当前就绪/确价/门槛通过offer，未完成不算低分，“本批最优”含比较范围。
- [x] 新测试红后实现outbox与逐渠道状态，业务事件key排除纯观察时间；外部发送与记录间窗口如实uncertain，不宣称exactly-once。
- [x] 全通知/offer/执行回归、审查后提交 `feat: deliver verified offer events independently`。

### P3.4：跨平台设备检测、资源profile与运行保护

**Files:** 新增 `goodprice/resources.py`、`tests/test_resource_profiles.py`；修改config.py、.env.example、锁文件、设置一致性测试；新增 `goodprice/web/resource_routes.py`。

**Interfaces:** `detect_resources() -> ResourceSnapshot`（effective_cpu:float|None、memory_limit_bytes:int|None、available_memory_bytes:int|None、source:str、warnings:tuple[str,...]、detected_at）；`recommend_profile(snapshot: ResourceSnapshot) -> dict`；`resource_profile(name: Literal['low','balanced']) -> dict` 仅返回并发字段；POST `/settings/resources/apply-profile` 验证后确认应用，GET `/api/resources/recommendation` 只读。

- [x] mock Windows/macOS/Linux/cgroup v1/v2 CPU quota/cpuset/memory.max与current、无限制/文件缺失/权限异常/祖先更低quota；容器优先有效较小值，不把宿主资源当容器资源。
- [x] 检测样本要覆盖cgroup从 `/proc/self/cgroup` 和mountinfo定位到当前进程而不是只读根目录；可用余量考虑宿主available与容器limit-current的较小值，不因cpuset字符串稀疏漏算。
- [x] 断言 CPU>=4、limit>=6GiB、available>=3GiB 才建议balanced，否则low；未知来源low；建议GET不改配置/不跑benchmark/不上传机器ID。
- [x] 断言profile低占用(2,1,1,1,1)、均衡(2,1,2,1,1)，不修改媒体数量、视频开关、平台间隔和RPM；无确认POST不应用。
- [x] 测试红后加入psutil正式依赖并同步两锁文件；实现能力可降级检测与运行时仅降档保护，保存值/当前有效值/原因分开显示。
- [x] 设备/配置/执行回归及审查后提交 `feat: recommend explicit cross-platform resource budgets`。

### P3.5：列表上下文与设置分类保存

**Files:** 新增 `goodprice/web/list_context.py`、`settings_sections.py`、`tests/test_list_context.py`、`tests/test_settings_sections.py`；修改routes.py、settings_service.py、列表/详情/设置模板，新增轻量静态交互文件。

**Interfaces:** `ListContext.from_query(query) -> ListContext`、`ListContext.url(path: str = '/listings') -> str`、`safe_return_to(raw: str | None, fallback: str) -> str`；`SettingsSection` 白名单仅account/models/notifications/resources/media/deployment_readonly；`save_section(section: str, form: Mapping[str,str]) -> Settings` 在单事务校验合并后保存。

- [x] 写任务/状态/价格状态/排序/加载范围roundtrip；非法/重复/超长参数规范化；外域、协议相对、编码换行/双编码return_to回标准列表，不用任意Referer。
  URL边界的最小断言：
  ```python
  assert safe_return_to("//example.com/listings", "/listings") == "/listings"
  assert safe_return_to("/listings?task_id=7&show=active", "/listings") == "/listings?task_id=7&show=active"
  ```
- [x] 写仅保存media不改变notification复选框；本分类未勾才关闭；秘密空值保持、显式清除有确认；数据库/容器运行模式等部署字段拒绝Web修改；无效值不提交半套配置。
- [x] 新用例红后实现规范URL链接贯通卡片、详情返回及所有操作重定向；分类用锚点/轻量tab保持当前分类，服务端白名单控制写入，不再遍历全部Settings做公共表单。
- [x] 分类支持unsaved提示、失败保留非秘密输入，清楚显示新增预算的代价和生效范围；沿用现有CSS/HTMX，无前端设计技能。
- [x] API/设置/所有页面渲染回归、审查后提交 `fix: retain listing context and isolate settings sections`。

### P3.6：全站功能交互回归

**Files:** 新增 `tests/e2e/test_navigation.py`、`test_settings.py`、`test_task_feedback.py`、`tests/e2e/conftest.py`；修改列表/卡片/详情/任务/通知/仪表盘/base模板与必要API。UI功能测试沿用Playwright，不用视觉重设计技能。

**Interfaces:** E2E fixture 只启动显式临时Settings、禁用真实scheduler/通知/模型、使用动态未占用回环端口，浏览器退出后清理；sessionStorage按规范查询key保存加载范围与锚点，秘密禁止保存。

- [x] 先写失败流程：筛选→加载更多→详情→页面返回/原生后退→刷新，断言task/status/sort/已加载卡片范围保留；随后删除/拉黑/重评返回相同上下文，被删锚点恢复到相近项。
- [x] 测试轮询不清选中项/正在编辑表单/当前滚动；加载更多稳定排序同分用ID，卡片无重复；交互时暂停刷新并给“有新结果，点击更新”。
- [x] 测试任务重复运行去重/暂停反馈、通知pending/uncertain状态、失败HTTP不会伪成功、全选只针对已加载项、设置分类返回与unsaved提示。
- [x] 各主要页面测试窄屏无横向溢出、键盘可达、禁JS核心GET筛选/POST表单可用；实现最小模板/JS修复，保留风格。
- [x] 浏览器回归通过后审查并提交 `fix: preserve navigation and improve simple page interactions`。

### P3.7：全项目审计、性能对比与隔离部署验收

**Files:** 扩展 `scripts/benchmark_pipeline.py`、`tests/test_pipeline_regressions.py`、`.github/workflows/ci.yml`；修改README、Dockerfile/compose必要参数化、相关发现的具体模块。新增 `docs/validation/variant-media-upgrade.md` 保存脱敏方法/结果，不提交私人机器信息。

**Interfaces:** benchmark `--dataset synthetic-30 --rounds 5 --mode cold|warm --output <temporary-dir>`，输出JSON包含浏览器启动数、SQL、模型HTTP、阶段中位/P95、资源峰值、处理/待处理/失败数量；必须比较同输入预算和相同业务判定结果。

- [x] 每项新发现bug先最小失败测试；重点旧库/启动副作用/后台重评绕过队列/interval更新/下架误判/长事务/N+1/资源泄漏/配置commit前校验，不能仅重构不证明症状。
- [x] 运行冷/热两轮对照基准，保证缓存轮不重复相同媒体模型调用、浏览器不逐商品重启；报告无明显回归或如实解释开销，不制造任意提速百分比。
- [x] CI按Windows/Linux配置单测与浏览器功能验证；requirements.lock用jev extra重新导出与uv.lock比较，镜像SDK/FFmpeg可用；不在CI中推镜像，release的测试门槛与main保持一致。
- [x] 运行 `uv lock --check`、`uv run --locked ruff check .`、`uv run --locked pytest -q --cov --cov-fail-under=80` 和E2E；迁移临时旧库两遍、还原备份演练通过。
- [x] Docker以唯一测试镜像/容器名、独立临时卷和空通知配置构建启动；检查healthz、表单、SDK/FFmpeg、视频开关、noVNC开/关，绝不使用生产compose up替换正在运行的服务。
- [x] 整分支独立代码审查、修复并完整复验，审查真实样本范围、缓存/预算、生命周期、API/旧库兼容。报告未验证macOS/ARM/平台样本限制，不冒充全部完成。
- [x] 提交 `test: validate upgrade performance and deployment compatibility`，填写总计划完成记录；用户选择集成前保持独立分支，不自动push/发版。

验收：见 `docs/validation/2026-10-27-phase3-milestone.md`。按已批准的阶段末一次独立审查执行；四项Important已RED→GREEN并复验，未自动集成/发布。
