# 闲鱼盯价助手（good-price）

一个开源的本地 Web 工具：盯住闲鱼上你感兴趣的关键词，价格符合预期且品相达标的新上架商品会自动收录并推送微信提醒。

## 功能

- **关键词盯价**：为每个监控任务设置关键词、最高价、品相要求与最低品相分
- **定时抓取**：APScheduler 按可配置间隔（默认 20 分钟 + 随机抖动）扫描闲鱼搜索页新上架商品
- **LLM 品相分析**：把商品标题、价格、描述和图片发给 OpenAI 兼容的多模态大模型，返回品相分（1-10）、瑕疵列表、是否推荐与一句话理由；品相分低于阈值不提醒
- **去重与通知**：同一商品只提醒一次；命中后站内记录，并通过 Server酱推送到微信（可插拔通知通道，内置日志通道）
- **本地 Web 界面**：仪表盘、监控任务管理、命中列表、设置页；服务端渲染，无 Node 构建链
- **降级策略**：未配置 LLM 或 LLM 调用失败时，自动降级为仅按价格命中

## 快速开始

要求：已安装 [conda](https://docs.conda.io/)、Git。

```bash
# 1. 克隆项目（或直接在项目目录内执行）
git clone <你的仓库地址> good-price
cd good-price

# 2. 创建 conda 环境并安装依赖
conda env create -f environment.yml

# 3. 安装 Playwright 浏览器（首次）
conda run -n good-price python -m playwright install chromium

# 4. 复制环境变量模板（可选，多数配置可在 Web 界面完成）
copy .env.example .env

# 5. 启动
conda run -n good-price python -m goodprice
```

浏览器打开 <http://127.0.0.1:8000>。

## 获取闲鱼 Cookie

1. 用浏览器（建议 Chrome/Edge）登录 <https://www.goofish.com>
2. 按 `F12` 打开开发者工具 → Network（网络）面板
3. 刷新页面，任选一个请求，在 Headers 里找到 `Cookie` 字段，整段复制
4. 粘贴到本工具的「设置」页面（或写入 `.env` 的 `XIANYU_COOKIE`）

> Cookie 会过期，过期后工具会记录错误提示，重新复制即可。

## 配置说明

所有配置都可以在 Web 界面的「设置」页修改，也会持久化到数据库；`.env` 中的值作为默认值。

| 配置项 | 说明 |
| --- | --- |
| `XIANYU_COOKIE` | 闲鱼登录 Cookie |
| `LLM_BASE_URL` | OpenAI 兼容服务地址，如 `https://dashscope.aliyuncs.com/compatible-mode/v1`（通义千问） |
| `LLM_API_KEY` | 大模型 API Key |
| `LLM_MODEL` | 模型名，默认 `qwen-vl-max`（多模态）；也支持 `gpt-4o-mini` 等 |
| `SERVERCHAN_SENDKEY` | Server酱 SendKey（<https://sct.ftqq.com>），留空则只写日志 |
| `PROXY` | 可选 HTTP 代理，如 `http://127.0.0.1:7890` |
| `DEFAULT_CRAWL_INTERVAL_MINUTES` | 默认抓取间隔（分钟） |
| `DEFAULT_CRAWL_JITTER_MINUTES` | 请求随机抖动（分钟），降低风控概率 |

## 开发与测试

```bash
conda run -n good-price pytest -v
```

## 架构

单进程一体化：FastAPI 提供 Web 界面与 JSON API，APScheduler 在进程内调度抓取任务，SQLAlchemy + SQLite 持久化。

- `goodprice/crawler/`：平台适配器协议 + 闲鱼 Playwright 适配器 + HTML 解析（选择器集中维护，平台改版只改适配器）
- `goodprice/analysis/`：OpenAI 兼容 LLM 客户端与品相分析提示词
- `goodprice/notify/`：通知通道协议（日志、Server酱；可扩展邮件/钉钉）
- `goodprice/services/`：设置服务（env 默认值 + 数据库覆盖）、任务服务、核心爬取流水线
- `goodprice/web/`：Jinja2 + HTMX + Tailwind（CDN）页面与路由

## 合规与免责声明

- 本工具仅供个人学习与研究使用，请遵守闲鱼及相关平台的服务条款。
- 使用自己账号的登录态、控制抓取频率（默认带随机抖动），风险自负。
- 本项目不存储、不上传任何第三方平台的账号密码；Cookie 仅保存在本地数据库中。
- 若因使用本工具产生账号限制或其它问题，作者不承担任何责任。

## 路线图

- [ ] 转转等平台适配器
- [ ] 单品盯价（收藏链接盯降价/下架）
- [ ] 邮件、钉钉/飞书通知通道
- [ ] 价格走势图表
- [ ] Docker 一键部署
