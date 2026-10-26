# 规格价格、媒体与流水线升级实施总计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将单标价图片盯价工具升级为按规格、可信价格和媒体证据判断、可配置资源且保持简洁交互的跨平台应用。

**Architecture:** 保留单进程 FastAPI/SQLite/Playwright 与服务端模板。分三个可独立测试的子计划实施，先完成纯领域裁决与迁移，再加入媒体，最后整合有界流水线与全站交互；不引入 Redis/Celery/SPA。

**Tech Stack:** Python >=3.11、FastAPI、SQLAlchemy/SQLite、Playwright、Jinja2/HTMX、pytest/Ruff；阶段二新增 Pillow、FFmpeg，阶段三新增 psutil。

**Spec:** `D:/good-price/docs/superpowers/specs/2026-10-26-variant-price-media-design.md`（已获用户批准）。

## Global Constraints

- 不使用前端设计类技能；保留简洁的颜色、布局基础与服务端模板，只改善信息与交互行为。
- Windows/macOS/Linux/NAS/Docker 均不以操作系统专属 shell/fork 为前提；Windows 本地和 Linux Docker 实测，其余未测明示。
- 不读取／提交真实 Cookie、密钥、私人文档，不挂载生产 data/ 进行测试，不发送真实通知，不自动触发付费模型调用。
- 不更改／删除本地忽略的 `.cmd`、PowerShell 启动器，不自动 push main/release、创建 Tag 或发布镜像。
- 价格未知不视为零，不按起售价排名，不自动联系卖家；必要证据过期暂停正常推荐。
- 待询价外部通知默认关闭，人工报价默认 7 天；视频默认关闭；旧安装总视觉输入保留 4，新安装为 8（混合 4+4）。
- 低占用首次默认：active runs=2、抓取/模型/媒体 worker 各 1、FFmpeg 线程=1；硬件建议需确认才能应用。
- 不默默放宽访问和资源预算；导航间隔默认 10 秒（5–300），模型 RPM 默认 10（1–60），并发和大小有服务端硬上限。
- 默认每轮详情 30、模型 HTTP 尝试 60、墙钟 15 分钟；默认工作项全局 200／每任务 60；取自设计 §6.1。
- 在新架构完成前保留阶段一/二已通过的交付；独立阶段提交可审查，但不把部分功能误标为整轮完成或正式稳定版。
- Ruff、锁文件一致性、全套测试与 >=80% 覆盖率、迁移、隔离 Docker 和独立审查均是交付门槛，不靠覆盖率取代行为用例。

## Review Focus

1. import app／pytest 收集意外初始化真实数据库：P1.1 用子进程与拒绝路径访问验证。
2. 价格来源切换伪造降价、任务目标变更沿用报价：P1.3/P1.4 以 observation/quote 与任务版本测试。
3. 临时媒体文件绕过磁盘预算、模型输出引用不存在证据：P2.2/P2.4 分别覆盖。
4. 暂停／到期之后迟到 worker 仍写入或发通知：P3.1/P3.2/P3.3 使用假时钟和受控 Future 验证。
5. 设置分类遗漏复选框清掉其他分类、return_to 开放重定向：P3.5 做 API 和真实浏览器用例。

## 计划拆分与执行次序

| 顺序 | 子计划 | 交付 |
| --- | --- | --- |
| 1 | `2026-10-26-phase1-offer-pricing-plan.md` | 测试隔离、规格/可信价/人工报价、旧库迁移、现有流水线接入 |
| 2 | `2026-10-26-phase2-media-evidence-plan.md` | 可配置媒体预算、安全下载/抽帧、证据分析与缓存 |
| 3 | `2026-10-26-phase3-pipeline-interaction-plan.md` | 受控流水线、设备建议、全站交互、综合性能和缺陷修复 |

每个子计划开始前先阅读本总计划和 Spec；后续计划通过下列契约衔接，不平行改同一大文件。

## 共享接口与文件边界

- `goodprice/offers/types.py`：不可变 PriceClaim/OfferCandidate/PriceDecision/QuoteInput/OfferEvaluation，不能携带 ORM session。
- `goodprice/offers/pricing.py`：金额、价格状态、有效来源与新鲜度裁决；`matching.py`：目标组合匹配与稳定身份。
- `goodprice/offers/service.py`：短事务保存报价、观察与评估；业务规则调用纯函数，模板不重复判断。
- `goodprice/media/types.py`：MediaCandidate/MediaEvidence/MediaSelection；`budget.py`：输入与磁盘预算，`download.py`：远端边界，`frames.py`：受限本地解码。
- `goodprice/execution/types.py`：Stage/RunToken/WorkResult；`store.py`：持久化状态；`limits.py`：并发/限流；`coordinator.py`：唯一派发与写入协调。
- `goodprice/web/list_context.py`：规范化列表状态；`settings_sections.py`：可写分类白名单；拆分新增路由文件，不把所有逻辑继续堆到 routes.py。
- `goodprice/resources.py`：只读跨平台设备检测、建议与可见有效限制；不代替平台/模型限流。
- 继续使用原 `models.py`、`db.py` 注册新表与迁移入口。领域和执行逻辑放独立包，避免循环导入。
- `CrawlService` 阶段一/二是迁移适配层；阶段三接到协调器，最终不允许旧队列与新协调器双消费。

## 安全执行与基线

- [ ] 实施前读取 using-git-worktrees 技能，使用原生 managed worktree 从已批准设计提交建隔离 checkout；不复制 .env 或 data/。
- [ ] 检查远程 main 的新变化与本地设计基线差异，仅 fetch 不自动合并社区新代码；必要冲突先解释。
- [ ] 先执行 P1.1，之后才运行导入 `goodprice.main` 的测试或启动服务。
- [ ] 基准使用合成页面、媒体、假模型和假时钟，保留请求次数、SQL、浏览器启动次数、中位数/P95及资源峰值；不压测真实平台。
- [ ] 每任务遵循红→绿→审查→修正→提交；每阶段独立审查并跑全量回归，发现超范围架构问题先更新设计。

## 交付记录

- [ ] 阶段一完成，记录 commit、命令、结果和真实 SKU 样本验证范围。
- [ ] 阶段二完成，记录 Linux/Windows FFmpeg 测试、视频适配限制与全部预算行为。
- [ ] 阶段三完成，记录队列峰值/重启回归、浏览器交互结果、性能对比和剩余问题。
- [ ] 整分支独立审查与最终复验完成；仅此时总结完成范围，未验证真实平台能力保持实验性。
- [ ] 用户选择集成／发版方式前，不 push main/release，不升级正在运行的生产容器或数据库。

## 当前状态

设计与计划已批准，采用主线程持续实施、阶段末独立审查。阶段一的规格/价格核心和阶段二媒体核心已实施并记录验证，
真实闲鱼规格与媒体控件仍待脱敏样本验证。阶段三已实施导航/设置整理、查询批量化、设备检测及执行/通知基础组件，
协调器尚未替换正式应用的旧队列；预算/限速、浏览器所有权、恢复与投递的完整集成、性能对比和最终独立审查仍是必需工作。
不能将基础模块单测通过写成整项升级完成；不自动推送、发版或替换生产服务。

## 自审覆盖记录

- Spec §4 的规格/金额/报价/来源/过期/迁移由 P1.2–P1.5 实现，§5 的媒体数量/代价/安全/缓存/证据由 P2.1–P2.5 实现。
- Spec §6.1 的恢复/背压/限流/取消与通知由 P3.1–P3.3，§6.2 的设备/容器限额由 P3.4，§6.3 的全站交互由 P3.5–P3.6 实现。
- Spec §7–8 的旧库/隔离部署/性能/跨平台验收由 P1.1/P1.2/P3.7 覆盖；私人文件与不发布限制全阶段适用。
- 接口从 offers/types → media/types → execution/types 顺序定义；任何阶段需要改变前置契约，须同步消费者测试，而非另造平行实现。
- 默认预算及用户确认边界均引用批准设计；未取得真实多规格/视频样本时，明确保留实验性限制，不视作适配验收通过。
