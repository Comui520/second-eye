# second-eye：闲鱼盯价助手

[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11-blue)](https://www.python.org/)
[![CI](https://github.com/Comui520/second-eye/actions/workflows/ci.yml/badge.svg)](https://github.com/Comui520/second-eye/actions/workflows/ci.yml)

一个面向中文二手市场的本地开源工具：按关键词和价格区间监控闲鱼商品，使用 LLM 进行需求匹配、品相分析和批量性价比判断，并通过多种通知渠道推送结果。

项目以 **Python + FastAPI + SQLite + Playwright** 为核心，不需要 Node.js/npm。Cookie、任务和分析结果默认保存在部署机器的本地数据库中。

> 本项目仅供个人学习和研究使用。请遵守闲鱼及相关平台的服务条款，控制访问频率并自行承担账号使用风险。

## 功能概览

- 关键词、价格区间、排除词和品相要求监控
- 需求匹配、图片品相分析、批量性价比判断
- 降价重评估和重复通知抑制
- 商品与卖家信息、风险提示、价格变化和通知历史
- Server酱、企业微信机器人、飞书机器人、Gotify 等通知渠道
- Web 设置页、任务队列、运行日志和 SQLite 持久化
- 可选 Jev 类型化判断层：逐件评估、置信度裁决和低置信度回落
- Docker 镜像、noVNC 登录模式和 GitHub Actions CI

## 支持的平台与部署方式

| 方式 | Windows | macOS | Linux / NAS | 推荐场景 |
| --- | --- | --- | --- | --- |
| Docker Compose | 支持 Docker Desktop | 支持 Docker Desktop | 支持 Docker Engine + Compose | 最可复现的部署方式 |
| uv 本地运行 | 支持 | 支持 | 支持 | 开发、调试、需要本机浏览器登录 |
| Conda 本地运行 | 支持 | 理论上支持 | 理论上支持 | 已经使用 Conda 的环境 |

Docker 是推荐的通用部署方式：应用、Python 依赖、Chromium 和 noVNC 都封装在镜像中，Windows、macOS、Linux 和 NAS 使用同一套 Compose 配置。

本仓库不再把 Windows 双击启动器作为公共安装接口。仓库中曾经使用的 `.cmd`/PowerShell 启动器只适合作者自己的 Windows 工作流，已从 Git 追踪中排除；它们不会影响 Docker Compose 和跨平台手动启动方式。

## 快速开始：Docker Compose

### 1. 获取代码并创建配置

~~~bash
git clone https://github.com/Comui520/second-eye.git
cd second-eye
cp .env.example .env
~~~

Windows PowerShell 可以使用：

~~~powershell
Copy-Item .env.example .env
~~~

编辑 .env，至少配置一个 OpenAI 兼容的文本模型。也可以先保持空配置，启动后在 Web 设置页填写。

### 2. 构建并启动

~~~bash
docker compose up -d --build
~~~

打开：

~~~text
http://127.0.0.1:18000
~~~

查看日志和停止服务：

~~~bash
docker compose logs -f second-eye
docker compose down
~~~

首次构建会下载 Python 依赖、Chromium、Debian 系统包和 noVNC 组件，耗时取决于网络；后续构建会复用 Docker 缓存。

### 3. Docker 登录

Docker 内的 Chromium 不会弹出到宿主机桌面，需要通过 noVNC 网页完成一次登录。默认 noVNC 关闭，避免把远程桌面长期暴露出去。

Linux/macOS/NAS 可以使用仓库提供的跨平台辅助脚本：

~~~bash
./scripts/start-docker.sh --login
~~~

脚本会生成 data/.novnc-password，启动容器并提示访问地址。默认访问（端口可用 NOVNC_PORT 修改）：

~~~text
http://127.0.0.1:16080/vnc.html
~~~

完成登录后，关闭 noVNC 并保持应用运行：

~~~bash
./scripts/start-docker.sh
~~~

Windows Docker Desktop 可以直接使用 Compose 完成相同操作：

~~~powershell
New-Item -ItemType Directory -Force data | Out-Null
Set-Content -Path data/.novnc-password -Value "请替换为至少 8 位密码" -NoNewline
$env:ENABLE_NOVNC = "1"
docker compose up -d --build
Start-Process http://127.0.0.1:16080/vnc.html
~~~

登录完成后执行：

~~~powershell
$env:ENABLE_NOVNC = "0"
docker compose up -d
~~~

noVNC 只建议在登录期间开启，不要直接暴露到公网。应用本身没有内置用户认证；如果需要局域网或公网访问，请在反向代理、VPN 或访问控制层增加认证。

### 4. NAS / Linux 长期运行

在 NAS 或 Linux 主机上，将 .env 中的 BIND_ADDRESS 设置为主机的局域网地址，例如：

~~~env
BIND_ADDRESS=192.168.1.20
~~~

然后启动：

~~~bash
./scripts/start-docker.sh
~~~

访问：

~~~text
http://192.168.1.20:18000
~~~

需要重新登录时临时执行 ./scripts/start-docker.sh --login，完成登录后再次执行不带 --login 的命令关闭 noVNC。

不要把 BIND_ADDRESS 设置为 0.0.0.0 后直接映射到公网。默认应用和 noVNC 都没有公网安全防护。

## 使用预构建 GHCR 镜像

正式版本会由 GitHub Actions 构建并推送到 GHCR。当前版本示例：

~~~bash
docker pull ghcr.io/comui520/second-eye:0.1.0
~~~

如果希望 Compose 使用预构建镜像，可以在启动前设置镜像变量：

~~~bash
SECOND_EYE_IMAGE=ghcr.io/comui520/second-eye:0.1.0 docker compose pull second-eye
SECOND_EYE_IMAGE=ghcr.io/comui520/second-eye:0.1.0 docker compose up -d --no-build
~~~

PowerShell：

~~~powershell
$env:SECOND_EYE_IMAGE = "ghcr.io/comui520/second-eye:0.1.0"
docker compose pull second-eye
docker compose up -d --no-build
~~~

如果 GHCR 包是私有的，先执行 docker login ghcr.io。镜像标签使用不带 v 的版本号，例如 Git Tag v0.1.0 对应镜像标签 0.1.0。

## 本地运行：uv

本地运行适合开发和调试，也可以直接使用宿主机浏览器完成登录。需要 Python 3.11 或更高版本，以及 uv。

~~~bash
uv sync --extra dev
uv run python -m playwright install chromium
uv run python -m goodprice
~~~

访问：

~~~text
http://127.0.0.1:8000
~~~

测试：

~~~bash
uv run pytest -q
~~~

如果系统尚未安装 uv，可以按 uv 官方文档安装；本仓库不静默安装系统级运行时。Windows、macOS、Linux 的命令行用法基本一致，但不同系统的浏览器、权限和代理配置可能不同。

## 本地运行：Conda

已有 Miniconda/Anaconda 的用户可以使用：

~~~bash
conda env create -f environment.yml
conda activate good-price
python -m playwright install chromium
python -m goodprice
~~~

已有环境需要同步依赖时：

~~~bash
conda activate good-price
python -m pip install -e ".[dev]"
python -m playwright install chromium
python -m goodprice
~~~

Conda 不是运行本项目的必需条件；新用户优先选择 Docker 或 uv。

## 配置

配置文件：

~~~text
.env.example   配置模板
.env           本地配置，不要提交
~~~

主要配置项：

| 配置项 | 作用 |
| --- | --- |
| XIANYU_COOKIE | 闲鱼登录 Cookie；也可以在设置页使用一键登录 |
| LLM_BASE_URL / LLM_API_KEY / LLM_MODEL | OpenAI 兼容文本模型 |
| VISION_BASE_URL / VISION_API_KEY / VISION_MODEL | 图片品相分析模型；不配置时跳过品相分析 |
| SERVERCHAN_SENDKEY | Server酱通知 |
| WECOM_WEBHOOK | 企业微信群机器人 |
| FEISHU_WEBHOOK / FEISHU_SECRET | 飞书机器人及可选签名密钥 |
| GOTIFY_URL / GOTIFY_TOKEN | Gotify 通知 |
| JEV_ENABLED | 启用实验性的 Jev 判断层 |
| JEV_BACKEND / JEV_API_KEY | Jev 后端和可选官方服务密钥 |
| PROXY | Docker 构建及运行时使用的 HTTP 代理 |
| BIND_ADDRESS | Docker 端口绑定地址，默认 127.0.0.1 |
| DEFAULT_CRAWL_INTERVAL_MINUTES | 默认抓取间隔 |
| DEFAULT_CRAWL_JITTER_MINUTES | 请求随机抖动 |

Web 设置页中的配置会保存到数据库，并覆盖 .env 中的默认值。不要把 .env、Cookie、API Key 或 data/ 提交到仓库。

## 数据与备份

Docker 和本地运行都会在项目的 data/ 目录中保存 SQLite 数据库、Cookie、日志和 noVNC 密码文件。建议：

- 定期备份 data/goodprice.db；
- 不要把 data/ 目录上传到公共仓库；
- 不要把 noVNC 端口暴露到公网；
- 迁移到 NAS 时同时迁移数据库和配置，并检查文件权限。

## 发版与分支

- main：日常开发和合并 Pull Request；
- release：稳定版本准备和验证；
- vX.Y.Z：正式 Git Tag 和 GitHub Release。

推送版本 Tag 后，GitHub Actions 会运行测试、构建 Docker 镜像并推送到 GHCR：

~~~bash
git switch release
git pull --ff-only origin release
git tag -a v0.1.1 -m "Release v0.1.1"
git push origin v0.1.1
~~~

普通 PR 和 main/release 推送会执行测试和 Docker 构建校验，但不会覆盖正式镜像。

## 开发与测试

使用 uv：

~~~bash
uv sync --extra dev
uv run pytest -q
~~~

使用 Conda：

~~~bash
conda activate good-price
python -m pytest -q
~~~

Docker 构建校验：

~~~bash
docker compose config --quiet
docker compose build second-eye
~~~

## 项目结构

~~~text
goodprice/crawler/       闲鱼适配器、Playwright 登录与解析
goodprice/analysis/      LLM、视觉分析和 Jev 判断层
goodprice/notify/        通知渠道
goodprice/services/      设置、任务、抓取流水线和队列
goodprice/web/           Web 路由和模板
tests/                   自动化测试
scripts/start-docker.sh  Linux/macOS/NAS 的 Docker 辅助脚本
.github/workflows/       CI 与 GHCR 发版流程
~~~

## 免责声明

- 本工具不保存第三方平台账号密码，只保存用户主动提供或登录产生的 Cookie。
- 请遵守目标平台服务条款，合理控制请求频率。
- 因使用本工具导致的账号限制、数据丢失或其他问题由使用者自行承担。

## 路线图

- [ ] 更多二手平台适配器
- [ ] 收藏链接盯降价 / 下架
- [ ] 更丰富的价格走势图表
- [ ] 通知图片和图文消息
- [ ] 商品视频解析
