# 阶段二：可配置媒体证据 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 安全识别商品图片/视频、受限抽帧，以用户确认的输入预算形成可追溯分析，而非虚构已看完整视频。

**Architecture:** 媒体模块从详情提取结果生成规范证据；受控下载器和本地 FFmpeg 处理，配额管理器统一计算临时/永久文件；以 data URL 复用现有 chat/responses 客户端。

**Tech Stack:** Python >=3.11、httpx、Pillow、FFmpeg/ffprobe、SQLAlchemy、pytest；Docker 显式安装 FFmpeg，本地缺工具优雅降级。

**Spec:** `D:/good-price/docs/superpowers/specs/2026-10-26-variant-price-media-design.md` §5、§6.2、§7–9；前置为阶段一计划完成。

## Global Constraints

继承总计划。视频默认关闭；默认保留8原图/1视频/每视频6帧，请求原图配额4/帧配额4/总上限8，旧安装总上限保留4；不自动多批收费。视频100 MiB/180秒、下载60秒/解码30秒、缓存1 GiB/7天，范围严格按 Spec §5.2/§6.2。

## Review Focus

- 签名 URL 刷新不等于媒体变化，下载失败不缓存成成功（P2.1/P2.3）。
- DNS 重绑定/重定向内网、图片解码炸弹、本地路径穿越（P2.2/P2.3）。
- 两个200 MiB任务突破256 MiB缓存、清理器删活跃输入（P2.2）。
- 模型虚构 evidence ID 或拿样品评分代表具体 SKU（P2.4）。
- 应用硬件 profile 无意提升旧安装4张上限、视频缺工具导致启动失败（P2.1/P2.5）。

### P2.1：媒体契约、数量配置与提取

**Files:** 新增 `goodprice/media/{__init__,types,selection}.py`、`tests/test_media_selection.py`、`tests/test_media_parser.py`；修改 config.py、models.py、migrations.py、crawler/base.py/parser.py/xianyu.py、.env.example、设置一致性测试。

**Interfaces:**
- MediaCandidate：`key: str, kind: Literal['image','video'], url: str, offer_key: str | None, source_locator: str, poster_url: str | None`。
- MediaEvidence：`id: str, kind: Literal['image','frame'], content_hash: str, local_path: Path, offer_key: str | None, video_id: str | None, timestamp_ms: int | None`。
- MediaSelection：`items: tuple[MediaEvidence,...], omitted_ids: tuple[str,...], limitations: tuple[str,...]`。
- `select_media(images: list[MediaEvidence], frames: list[MediaEvidence], image_quota: int, frame_quota: int, total_limit: int) -> MediaSelection`：混合交替公平分配，单类型可借额，设0的媒体明确未分析。
- `extract_media_candidates(html: str) -> list[MediaCandidate]`：仅商品主媒体，未适配 blob/HLS/DRM 标记 unsupported，不自行下载。
- 新 MediaAsset ORM 记录来源、类型、父视频、处理状态、hash、时间位置；新设置名称统一用 `media_*`（数量/上限/超时）及 `video_analysis_enabled`。

- [ ] 写 `test_default_mixed_selection_is_four_images_four_frames`、`test_single_type_borrows_unused_quota`、`test_zero_quota_reports_omitted_type`、`test_invalid_or_over_model_limit_rejected`。
  对含至少4张有效原图和4帧的 fixture，断言：
  ```python
  selected = select_media(images, frames, image_quota=4, frame_quota=4, total_limit=8)
  assert len(selected.items) == 8
  assert sum(item.kind == "frame" for item in selected.items) == 4
  ```
- [ ] 写纯图/纯视频/混合/推荐流排除/非实际SKU媒体共享与顺序稳定测试；迁移旧配置总上限4且视频关闭，新安装8，既有显式值保留。
- [ ] 运行新测试确认红；实现类型、表迁移、配置字段和配额选择器，Settings 跨字段验证引用 Spec 精确范围，未知模型上限保守8，显式确认最高16。
- [ ] 运行媒体/配置/迁移测试，审查后提交 `feat: configure media evidence and input budgets`。

### P2.2：统一磁盘配额与安全下载

**Files:** 新增 `goodprice/media/budget.py`、`download.py`、`cache.py`、`tests/test_media_budget.py`、`tests/test_media_download.py`。

**Interfaces:**
- `MediaBudget(root: Path, max_bytes: int, retention_seconds: int)`，`reserve(key: str, bytes_needed: int)` 返回上下文管理器/预留token；`commit(token: str, paths: list[Path]) -> None`、`release(token: str) -> None`。
- `MediaDownloader(budget: MediaBudget, transport=None, resolver=None)` 的 `download(candidate: MediaCandidate, max_bytes: int, timeout_seconds: int) -> Path`；缓存命中由内容和处理参数决定，不只由 URL 决定。
- 请求使用经过验证/绑定的目的地址与原始 hostname TLS 校验；若底层不能防止解析后重绑定则域不安全，不能只 resolve 一次后普通 client.get。

- [ ] 写响应无 Content-Length/伪长度/重定向metadata/IPv6内网/解析后重绑定/凭据跨域/慢流/断流测试；失败半成品不存在，异常不含URL令牌和Cookie。
- [ ] 写256 MiB配额下两个200 MiB任务只有一个可派发，活动输入不被LRU删除，单任务预算不能容纳时明确报错，重启回收预留且不突破缓存根路径。
- [ ] 执行 `pytest tests/test_media_budget.py tests/test_media_download.py -q` 确认红。
- [ ] 实现受限流式下载与重定向逐跳校验，临时文件位于同一受控根目录；配额包含全部中间文件，预先保守估算输出，磁盘剩余不足则延后/拒绝，不偷偷删证据继续算成功。
- [ ] 运行新用例和媒体选择回归，审查网络与路径边界后提交 `feat: bound media downloads and cache disk usage`。

### P2.3：受限解码、抽帧与可复现缓存

**Files:** 新增 `goodprice/media/frames.py`、`processing.py`、`tests/test_video_frames.py`、`tests/test_media_processing.py`；修改 Dockerfile、pyproject.toml、uv.lock、requirements.lock、.dockerignore。

**Interfaces:** `probe_video(path: Path, timeout_seconds: int) -> VideoInfo`（duration_seconds/dimensions）；`extract_frames(path: Path, count: int, output_dir: Path, timeout_seconds: int, threads: int) -> list[FrameFile]`（path/timestamp_ms/hash）；`prepare_image(path: Path, max_edge: int = 1280) -> Path`；`MediaProcessor.process(candidates: list[MediaCandidate], settings: Settings) -> list[MediaEvidence]`。

VideoInfo 与 FrameFile 定义在 media/types.py，字段分别为 `duration_seconds: float, width: int, height: int` 和 `path: Path, timestamp_ms: int, content_hash: str`。配额 `reserve` 返回 `BudgetReservation(token: str, bytes_reserved: int)` 上下文对象；未commit的退出总会release。

- [ ] 合成小视频在临时目录生成并记录清晰帧时间位置；假子进程测试缺FFmpeg、进程超时、超180秒、输出超预留配额和非法协议输入；图片测试超解码像素、伪MIME和坏文件。
- [ ] 测试 content hash 相同只解码一次、签名URL变化内容相同不付费重算、重复/模糊帧不会补造达到数量；缓存被清理显示证据不可用，不返回坏路径。
- [ ] 运行新测试确认红；实现参数数组启动FFmpeg，禁止shell/网络输入，默认线程1、受控超时、有限输出，Pillow处理前先检查像素上限。
- [ ] Docker 安装FFmpeg；Pillow加入正式依赖，uv lock后以 `uv export --locked --no-dev --extra jev --no-emit-project --format requirements.txt --output-file requirements.lock` 同步镜像依赖，检查无无关升级。
- [ ] Windows真实工具可用时跑抽帧；否则标明缺失并在Linux隔离镜像跑真实工具测试。本地缺工具页面可打开，视频状态标为不可用。审查后提交 `feat: extract bounded video frame evidence`。

### P2.4：联合视觉分析与证据校验

**Files:** 新增 `goodprice/analysis/media_analysis.py`、`tests/test_media_analysis.py`；修改 analysis/llm.py/prompts.py、services/offer_pipeline.py、services/crawl_service.py、评估序列化。

**Interfaces:** `analyze_media(offer: OfferCandidate, selection: MediaSelection, client: LLMClient, requirement: str, request_limit_bytes: int) -> dict`；`validate_evidence_references(verdict: dict, allowed_ids: set[str]) -> dict`；结果含 observations、seller_claims、conflicts、unverified、condition_score 可空、evidence引用。

- [ ] MockTransport 断言 chat与responses输入使用可解码 data URL、精确选中数量及证据 ID，不含宿主路径；16 MiB上限超出则不发送请求，不静默缩减输入。
- [ ] 测试模型引用不存在ID被拒绝、共享样品不能产生SKU确定品相、视频失败仅图分析时标明覆盖限制、无输入不产生伪评分、最低分>0未知时待核验。
- [ ] 测试缓存 key 包含内容/任务条件/选图参数/模型/提示词版本；只刷新采集时间不重复模型调用，调整设置不自动重跑历史。
- [ ] 运行新用例确认红；实现证据提示词、输入构造、schema校验和精确缓存失效，与价格结果分开，不允许视觉模型反写实际售价。
- [ ] 跑全部LLM/Jev/offer/media测试及审查后提交 `feat: analyze mixed media with traceable evidence`。

### P2.5：媒体设置、证据展示与阶段验收

**Files:** 新增 `goodprice/web/media_routes.py`、`tests/test_media_api.py`；修改设置/商品详情模板、router注册、README、.env.example。

**Interfaces:** GET `/media/{asset_id}` 仅内部ID映射受控路径；GET `/api/media/capabilities` 返回FFmpeg可用性和有效预算，不返回宿主路径；媒体配置待阶段三分类保存统一管理，字段校验复用Settings。

- [ ] 写路径穿越/未知ID/文件清理返回404，页面显示抽帧时间位置/未分析/工具缺失；数量增大显示费用/延迟/内存/存储/模型限制，确认前不扩大旧4张预算。
- [ ] 运行新用例确认红；实现最小简洁表单和证据展示，生成HTML不泄露签名URL令牌，本地缺FFmpeg提供安装指引而不静默安装系统工具。
- [ ] 全套Ruff/锁文件/pytest覆盖率>=80%，隔离Docker构建和FFmpeg/SDK检测；媒体下载全为fake或受控测试资源，不挂生产data。
- [ ] 阶段独立审查安全与资源边界，修复回归并提交 `feat: expose media evidence and explain analysis costs`；记录真实平台未验证项，勾选总计划阶段二。
