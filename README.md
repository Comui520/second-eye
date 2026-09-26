# 闲鱼盯价助手（second-eye）

[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11-blue)](https://www.python.org/)

盯住闲鱼上你感兴趣的关键词：价格符合预期、品相达标、性价比高的商品会自动收录，并推送到你的微信或企业微信。
AI 品相筛选、卖家信用、降价重推、本地开源——一个面向中文二手市场的个人盯价工具。

> 本地单进程应用：FastAPI + SQLite + Playwright + LLM，一条命令启动，数据和 Cookie 只存在你自己电脑上。

## 功能

**核心能力**

- **关键词盯价**：每个监控任务可设关键词、价格区间、排除词与品相要求，定时扫描闲鱼新上架商品
- **三阶段 AI 筛选**：需求匹配（文本）→ 品相分析（看图，1-10 分）→ 本批横向性价比对比，标出「本批最优」
- **降价重推**：价格变化触发重评，满意度提高才再次推送并标明「价格更新重推」，无变化不打扰
- **卖家信用**：好评率、卖出件数、信用等级与评价标签（7 天缓存），风险分级随通知提示，只提示不拦截
- **消息通知**：Server酱、企业微信群机器人、飞书机器人与 Gotify，可独立开关；站内可查全部推送记录
- **本地安全**：一键登录抓 Cookie（免 F12，不保存密码），数据与 Cookie 只存本地 SQLite

**细节能力**

- 价格下限 + 排除词过滤超低价配件噪音；同名任务互不干扰（商品按 任务id + 外部id 去重）
- 串行任务队列：同一时刻只跑一个任务，任务间间隔 5 分钟，运行超时后自动补跑
- 更新重评估、多规格价格区间、连续 3 轮未见标记「已下架」
- 任务/商品详情页：改参数、看运行统计、价格走势、通知历史，支持手动「重新分析」
- 命中列表排序/筛选/加载更多、商品与卖家拉黑、控制台分步日志
- 评分公式：需求 40 + 品相 30 + 性价比 20 + 卖家 10（视觉关闭自动切换为 需求 50 + 性价比 30 + 卖家 20，较首见降价有加成）
- 视觉模型可选：未配置时跳过品相分析并注明；分析失败不拦截（宁多勿漏）

## 快速开始

项目是 Python/FastAPI 应用，不需要 Node.js/npm。仓库提供三个可直接点击的 Windows 启动脚本：

```text
start-conda.cmd   # Conda 环境
start-uv.cmd      # uv 环境
start-docker.cmd  # Docker 环境
```

它们会自动检查、创建或补齐项目环境，环境已经存在时会尽量复用。第一次运行需要下载依赖、Python 或 Chromium，耗时取决于网络；以后启动会快很多。

> 现实限制：脚本可以自动配置 Python 依赖，但不能可靠地替你安装操作系统级运行时。Conda 脚本要求电脑已经安装 Miniconda/Anaconda；Docker 脚本要求已经安装并启动 Docker Desktop。若电脑什么都没有，推荐先双击 `start-uv.cmd`，它会尝试自动安装 uv 和 Python 3.11。

### 三种启动方式怎么选

| 启动脚本 | 适合谁 | 是否需要手动安装 | 登录浏览器 | 典型用途 |
| --- | --- | --- | --- | --- |
| `start-uv.cmd` | 大多数 Windows 用户 | 脚本尝试自动安装 uv；需要网络 | Windows 原生浏览器窗口 | 最推荐的本机方式 |
| `start-conda.cmd` | 已经使用 Conda 的用户 | 需要先有 Miniconda/Anaconda | Windows 原生浏览器窗口 | 兼容原有 Conda 流程 |
| `start-docker.cmd` | 不想配置 Python 的用户 | 需要 Docker Desktop | noVNC 网页中的容器浏览器 | 本机快速体验 |

三种方式不要同时运行：它们默认共享 `data/goodprice.db`，同时启动可能导致任务重复执行或通知重复发送。

### 方式一：uv（本机推荐）

直接双击：

```text
start-uv.cmd
```

脚本会自动：

1. 检查 uv；找不到时尝试从官方安装脚本安装；
2. 使用 `pyproject.toml` 和 `uv.lock` 创建 `.venv`；
3. 安装 Python 依赖和 Playwright Chromium；
4. 启动应用。

也可以在 PowerShell 中运行：

```powershell
.\scripts\start-uv.ps1
```

启动后打开 <http://127.0.0.1:8000>。在「设置 → 一键登录」时会打开 Windows 本机浏览器窗口。

如果自动安装 uv 被网络或安全软件拦截，可以手动安装 uv 后再次双击脚本；脚本会复用已经存在的环境。

### 方式二：Conda（兼容原有流程）

确认已经安装 Miniconda 或 Anaconda 后，直接双击：

```text
start-conda.cmd
```

脚本会自动：

1. 查找 Conda（包括常见的用户目录安装位置）；
2. 如果没有 `good-price` 环境，按 `environment.yml` 创建；
3. 如果环境已存在，补齐当前项目依赖；
4. 安装或检查 Playwright Chromium；
5. 启动应用。

也可以运行：

```powershell
.\scripts\start-conda.ps1
```

Conda 没有安装时，脚本会明确提示原因；此时可以改用 `start-uv.cmd`，不需要为了本项目额外安装 Conda。

### 方式三：Docker（不需要配置 Python）

#### Windows 本机快速使用

直接双击：

```text
start-docker.cmd
```

这个 Windows 快捷脚本默认使用“登录模式”，会自动：

- 检查 Docker Desktop 和 Docker Compose；
- 创建 `.env`、`data/`；
- 构建并启动镜像；
- 自动生成 noVNC 密码；
- 打开设置页和 noVNC 页面。

在 noVNC 页面中完成闲鱼登录。登录地址是：

```text
http://127.0.0.1:16080/vnc.html
```

Docker 里的 Chromium **不会变成 Windows 桌面上的原生弹窗**；它显示在 noVNC 网页里的虚拟桌面中。登录完成后，执行普通启动关闭 noVNC：

```powershell
.\scripts\start-docker.ps1
```

普通访问地址为 <http://127.0.0.1:18000>。

如果希望手动控制：

```powershell
.\scripts\start-docker.ps1 -Login    # 临时开启 noVNC 并打开登录页面
.\scripts\start-docker.ps1           # 普通运行，关闭 noVNC
.\scripts\start-docker.ps1 -NoBuild # 不重新构建镜像，直接启动
```

#### NAS 或 Linux 长期运行

Docker 长期运行时默认关闭 noVNC。先在 `.env` 设置端口绑定地址，例如 NAS 局域网 IP：

```env
BIND_ADDRESS=192.168.1.20
```

然后运行：

```bash
./scripts/start-docker.sh
```

访问：

```text
http://192.168.1.20:18000
```

需要重新登录时临时执行：

```bash
./scripts/start-docker.sh --login
```

然后在局域网电脑打开：

```text
http://192.168.1.20:16080/vnc.html
```

登录完成后再次执行不带 `--login` 的命令，关闭 noVNC。不要把 noVNC 直接暴露到公网；如果通过反向代理发布，Web 页面和 noVNC 都应配置认证。

> 本机 Docker 和 NAS Docker 使用同一份代码、同一个 `compose.yml`，区别只在启动脚本、`BIND_ADDRESS` 和 noVNC 是否临时开启，不需要维护不同 Git 分支。

### 首次启动后的三步

1. 浏览器打开本工具：uv/Conda 是 <http://127.0.0.1:8000>，Docker 是 <http://127.0.0.1:18000>；
2. 进入「设置 → 一键登录」，按当前启动方式完成登录；
3. 在「设置 → 大模型」填写 OpenAI 兼容模型地址和 API Key，再到「监控任务」新建任务。

### 常见意外情况

#### 1. 双击后窗口一闪而过

优先从项目目录打开 PowerShell 执行对应脚本，这样可以看到完整错误：

```powershell
.\scripts\start-uv.ps1
.\scripts\start-conda.ps1
.\scripts\start-docker.ps1 -Login
```

仓库根目录的 `.cmd` 启动器已经使用 `ExecutionPolicy Bypass`，通常不需要修改 PowerShell 全局执行策略。

#### 2. uv、Conda 或 Docker 找不到

- `start-uv.cmd` 会尝试自动安装 uv；如果下载被代理或安全软件拦截，请手动安装 uv 后重试。
- `start-conda.cmd` 不会静默下载并安装 Conda；请安装 Miniconda/Anaconda，或者直接改用 `start-uv.cmd`。
- `start-docker.cmd` 不能替你安装 Docker Desktop；请先安装 Docker Desktop 并等待 Docker Engine 显示为 Running。

#### 3. 第一次启动很慢

这是正常的：需要下载 Python 依赖、Playwright Chromium，Docker 还需要构建镜像。后续启动会复用 `.venv`、Conda 环境、Docker 层和浏览器缓存。

#### 4. 端口被占用

默认端口如下：

| 用途 | 本地 uv/Conda | Docker |
| --- | ---: | ---: |
| Web | `8000` | `18000` |
| noVNC | 不使用 | `16080` |
| Gotify | 不使用 | `18080` |

如果端口被占用，可以先停止旧进程/容器；Docker 端口也可以在 `compose.yml` 中修改。不要让本地进程和 Docker 同时使用同一份 `data/`。

#### 5. Docker 登录页没有原生弹窗

这是预期行为，不是故障。Docker 中的 Chromium 不能弹到 Windows 桌面。请双击 `start-docker.cmd`，在自动打开的 noVNC 页面中操作容器浏览器；如果必须使用原生浏览器窗口，请使用 `start-uv.cmd` 或 `start-conda.cmd`。

#### 6. noVNC 打不开或密码不对

确认使用的是登录模式：

```powershell
.\scripts\start-docker.ps1 -Login
```

脚本会显示 noVNC 密码并写入 `data/.novnc-password`。如果容器启动失败，查看日志：

```powershell
docker compose logs --tail=100 second-eye
```

登录完成后执行不带 `-Login` 的普通启动，关闭 noVNC。不要把 noVNC 端口直接暴露到公网。

#### 7. Docker 构建出现 502、超时或 Chromium 下载失败

通常是 Docker、Debian 软件源、PyPI 或 Playwright CDN 的临时网络问题。重新运行启动器即可；网络受限时在 `.env` 配置：

```env
PROXY=http://127.0.0.1:7890
```

#### 8. 页面能打开，但任务没有通知

启动脚本只负责运行环境，不会自动配置模型和通知服务。请在「设置」页面填写 LLM、视觉模型和通知渠道；没有有效 Cookie 时也无法正常抓取闲鱼。

### Docker / NAS 构建说明

项目提供单容器 Docker 部署方式，浏览器依赖只在镜像构建时安装，运行数据通过 `data/` 持久化。构建需要访问 PyPI、Debian 软件源和 Playwright 下载地址；网络受限时可在 `.env` 设置 `PROXY`。依赖和浏览器层会被 Docker 缓存，后续只修改源码通常不会重复下载浏览器。

手动构建命令：

```bash
docker compose up -d --build
```

如果构建过程中出现 Debian 软件源 `502 Bad Gateway`、Playwright CDN 超时等错误，通常是临时网络问题，重新执行构建即可；必要时配置代理。

## 大模型配置

阶段一「需求匹配」使用现有 LLM 配置；阶段二「品相分析」需要视觉模型。推荐全部使用智谱免费模型：

- **文本（需求匹配）：GLM-4.7-Flash**（免费）：Base URL `https://open.bigmodel.cn/api/paas/v4`，模型 ID `glm-4.7-flash`
- **视觉（品相分析）：GLM-4.6V-Flash**（免费）：模型 ID `glm-4.6v-flash`，支持图片与视频输入；高峰期可能限流，工具会自动重试。备选 `glm-4.1v-thinking-flash`（免费，响应更稳定）
- 模型 ID 必须小写；其他视觉模型我们未实际使用过，暂不做推荐

未配置视觉模型或关闭「视觉品相分析」开关时，品相分析会被跳过并在通知中注明，评分自动切换为「需求 50 + 性价比 30 + 卖家 20」。

## 获取闲鱼 Cookie

**推荐方式：一键登录（免 F12）**

1. 本地 uv/Conda 启动：点击「设置 → 一键登录」，会打开本机浏览器窗口。
2. Docker 本机或 NAS 启动：先用 `start-docker.ps1 -Login` 或 `start-docker.sh --login` 临时开启 noVNC，再打开当前主机的 `:16080/vnc.html`。
3. 像正常上网一样扫码或用账号密码登录闲鱼（密码只输入在淘宝/闲鱼官方登录页，程序不保存密码）。
4. 登录成功后程序自动抓取 Cookie 并保存；登录态保存在 `data/`，下次可能免登录。
5. 登录完成后恢复普通启动，关闭 noVNC。

**手动方式**

1. 用浏览器（建议 Chrome/Edge）登录 <https://www.goofish.com>
2. 按 `F12` 打开开发者工具 → Network（网络）面板
3. 刷新页面，任选一个请求，在 Headers 里找到 `Cookie` 字段，整段复制
4. 粘贴到本工具的「设置」页面（或写入 `.env` 的 `XIANYU_COOKIE`）

> Cookie 会过期，过期后工具会记录错误提示，重新登录即可。

> Docker 部署的 noVNC 默认关闭；登录脚本会在本地生成 `data/.novnc-password`，且端口绑定由 `BIND_ADDRESS` 控制。登录完成后应关闭 noVNC。

## 企业微信群机器人（推荐，免费）

应用消息需要可信域名/回调 URL，家庭用户配置困难；群机器人只需一个 Webhook，无需域名和 IP 白名单：

1. 在企业微信里建一个群（自己拉自己即可），群设置 → 群机器人 → 添加机器人
2. 复制机器人 Webhook 地址，填入本工具「设置」页 →「消息通知」→「群机器人 Webhook」，并确认开关已勾选
3. 限制：每个机器人 20 条/分钟；消息发到企业微信群，手机装企业微信 App 即可收到通知

## Gotify（推荐自托管）

Docker Compose 会同时启动 Gotify 服务，默认地址为 <http://127.0.0.1:18080>。首次登录使用 `admin` / `admin`，登录后请立即修改密码。

1. 打开 Gotify Web 页面，进入 `Applications`
2. 创建一个应用并复制 Application Token
3. 在 second-eye「设置 → 消息通知」中填入 Gotify 地址、Token，并打开 Gotify 开关

容器内部地址填写 `http://gotify`；Gotify 管理页面默认只绑定宿主机 `127.0.0.1:18080`。如需从其它设备访问，应通过带认证的反向代理发布，或在确认网络边界后自行修改 Compose 端口绑定。Gotify 数据保存在独立的 Docker Volume 中。

## 飞书机器人

飞书自定义机器人使用 Webhook，支持文本消息和签名校验。将机器人 Webhook 与可选签名密钥填入「设置 → 消息通知」即可。Webhook 属于敏感凭据，请勿提交到 Git 或公开分享。

## Codex 中转

如果使用 OpenAI Responses API 兼容的 Codex 中转，可配置：

```env
LLM_BASE_URL=http://192.168.x.x:15722/v1
LLM_API_KEY=PROXY_MANAGED
LLM_MODEL=gpt-5.6-luna
LLM_API_FORMAT=responses
```

视觉模型仍需单独配置；Codex 文本模型不作为视觉模型使用。

## 卖家信用/评价

- 数据来源：商品详情页卖家区块（好评率、卖出件数、信用等级）+ 卖家主页「信用及评价」标签（好评数、评价标签统计）
- 缓存：每个卖家 7 天内只抓一次，避免频繁请求
- 风险分级：好评率 ≥98% 或「信用极好」→ 低；≥90% → 中；否则高；数据不足 → 未知
- 策略：风险只出现在通知和页面徽标中（绿/黄/红），**不会拦截通知**

## 配置说明

所有配置都可以在 Web 界面的「设置」页修改，并持久化到数据库；`.env` 中的值作为默认值。

| 配置项 | 说明 |
| --- | --- |
| `XIANYU_COOKIE` | 闲鱼登录 Cookie（推荐用「一键登录」获取） |
| `LLM_BASE_URL` | OpenAI 兼容服务地址，如 `https://open.bigmodel.cn/api/paas/v4`（智谱） |
| `LLM_API_KEY` | 大模型 API Key |
| `LLM_MODEL` | 模型名，阶段一需求匹配使用（推荐智谱 `glm-4.7-flash`，免费，小写） |
| `LLM_API_FORMAT` | `chat_completions` 或 `responses`；Codex bridge 使用 `responses` |
| `VISION_BASE_URL` / `VISION_API_KEY` / `VISION_MODEL` | 阶段二视觉模型（推荐智谱 `glm-4.6v-flash` / `glm-4.1v-thinking-flash`，免费，小写）；不填则跳过品相分析 |
| `SERVERCHAN_SENDKEY` | Server酱 SendKey（<https://sct.ftqq.com>），留空则只写日志 |
| `WECOM_WEBHOOK` | 企业微信群机器人 Webhook（推荐，无需域名/IP） |
| `FEISHU_WEBHOOK` / `FEISHU_SECRET` | 飞书自定义机器人 Webhook 与可选签名密钥 |
| `GOTIFY_URL` / `GOTIFY_TOKEN` / `GOTIFY_PRIORITY` | Gotify 服务地址、Application Token 和消息优先级 |
| `SERVERCHAN_ENABLED` / `WECOM_ROBOT_ENABLED` / `FEISHU_ENABLED` / `GOTIFY_ENABLED` / `VISION_ENABLED` | 通知和视觉分析独立开关 |
| `PROXY` | 可选 HTTP 代理，如 `http://127.0.0.1:7890` |
| `BIND_ADDRESS` | Docker 端口绑定地址；本机默认 `127.0.0.1`，NAS 局域网访问可填写 NAS 局域网 IP |
| `DEFAULT_CRAWL_INTERVAL_MINUTES` | 默认抓取间隔（分钟） |
| `DEFAULT_CRAWL_JITTER_MINUTES` | 请求随机抖动（分钟），降低风控概率 |

## 常见问题

- **Cookie 过期了怎么办？** 设置页重新「一键登录」即可；任务出错时黑窗和任务详情页会写明原因。
- **为什么有些商品没有品相分/性价比？** 品相分析需要视觉模型且有有效商品图；失败会「宁多勿漏」放行，详情页会注明原因，可点「重新分析」补跑。
- **免费视觉模型限流怎么办？** `glm-4.6v-flash` 高峰期可能返回 429，工具会自动重试；仍失败可临时切换 `glm-4.1v-thinking-flash`。
- **两个任务关键词一样会冲突吗？** 不会，商品按任务独立记录与通知，互不干扰。
- **搜索总超时或结果不对？** 先检查 Cookie 是否过期、代理是否可用；错误信息会写清楚是登录失效、页面改版还是网络问题。

## 开发与测试

```bash
conda run -n good-price pytest -v
```

## 架构

单进程一体化：FastAPI 提供 Web 界面与 JSON API，APScheduler + 串行任务队列调度抓取，SQLAlchemy + SQLite 持久化。

- `goodprice/crawler/`：平台适配器协议 + 闲鱼 Playwright 适配器 + HTML 解析 + 一键登录（选择器集中维护，平台改版只改适配器）
- `goodprice/analysis/`：OpenAI 兼容 LLM 客户端与品相/性价比提示词
- `goodprice/notify/`：通知通道协议（日志、Server酱、企业微信群机器人、飞书、Gotify）
- `goodprice/services/`：设置服务（env 默认值 + 数据库覆盖）、任务服务、核心爬取流水线、串行任务队列
- `goodprice/web/`：Jinja2 + HTMX + Tailwind（CDN）页面与路由

## 合规与免责声明

- 本工具仅供个人学习与研究使用，请遵守闲鱼及相关平台的服务条款。
- 使用自己账号的登录态、控制抓取频率（默认带随机抖动），风险自负。
- 本项目不存储、不上传任何第三方平台的账号密码；Cookie 仅保存在本地数据库中。
- 若因使用本工具产生账号限制或其它问题，作者不承担任何责任。

## 路线图

- [ ] 转转等平台适配器
- [ ] 单品盯价（收藏链接盯降价/下架）
- [ ] 企业微信智能机器人 WebSocket 通知
- [ ] 价格走势图表
- [ ] 通知图片上传与图文消息
- [ ] 商品视频解析（`glm-4.6v-flash` 支持视频输入，可作为后续增强）
