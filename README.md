# 闲鱼盯价助手（good-price）

一个开源的本地 Web 工具：盯住闲鱼上你感兴趣的关键词，价格符合预期且品相达标的新上架商品会自动收录并推送微信提醒。

## 功能

- **关键词盯价**：为每个监控任务设置关键词、最高价、品相要求与最低品相分
- **定时抓取**：APScheduler 按可配置间隔（默认 20 分钟 + 随机抖动）扫描闲鱼搜索页新上架商品
- **两阶段筛选**：先抓商品详情描述做「需求匹配」（纯文本，DeepSeek 也可用），匹配后再把图片发给视觉模型做「品相分析」（品相分 1-10、瑕疵、理由）；需求不匹配或品相分低于阈值不提醒
- **卖家信用/评价**：详情页抓卖家好评率/卖出件数/信用等级，卖家主页抓好评数/评价标签（每卖家缓存 7 天）；风险分级（低/中/高）随通知提示，只提示不拦截
- **视觉模型可选**：未配置视觉模型时自动跳过品相分析并注明，需求匹配仍生效
- **去重与通知**：同一商品只提醒一次；命中后站内记录，并通过 Server酱推送到微信（可插拔通知通道，内置日志通道）
- **任务管理**：任务的增删改查齐全（编辑页可改关键词/价格/需求/间隔等），点击任务名直达该任务命中的商品
- **拉黑机制**：商品或卖家可拉黑，拉黑后不再处理/通知；命中列表可筛选未拉黑/已拉黑/全部
- **命中排序**：默认按「满足程度」组合评分（需求匹配 50 + 品相分 40 + 卖家风险 10），也可按价格升/降序或最新排序
- **本地 Web 界面**：仪表盘、监控任务管理、命中列表、设置页；服务端渲染，无 Node 构建链
- **降级策略**：需求分析/详情抓取失败时不拦截（宁多勿漏）；阶段一使用文本模型即可

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

## 企业微信通知配置（推荐，免费）

1. 用微信扫码注册/登录[企业微信管理后台](https://work.weixin.qq.com)（个人可免费创建企业，未认证不影响 API）
2. 「应用管理」→「自建」→ 创建应用，拿到 `AgentId` 和 `Secret`；`CorpID` 在「我的企业」页
3. 在应用详情页把本机出口 IP 加入「企业可信 IP」（家庭宽带 IP 变化后需更新）
4. 在「我的企业」→「微信插件」邀请自己的微信加入，之后企业微信应用消息会推到微信
5. 把四个参数填入本工具「设置」页（接收人默认 `@all`，也可填自己的 userid）

企业微信推送完全免费，额度远高于个人使用场景；出错时任务页会显示具体原因（如 IP 不在可信列表）。

## 企业微信群机器人（推荐）

应用消息需要可信域名/回调 URL，家庭用户配置困难；群机器人只需一个 Webhook，无需域名和 IP 白名单：

1. 在企业微信里建一个群（自己拉自己即可），群设置 → 群机器人 → 添加机器人
2. 复制机器人 Webhook 地址，填入本工具「设置」页的「群机器人 Webhook」
3. 限制：每个机器人 20 条/分钟；消息发到企业微信群，手机装企业微信 App 即可收到通知

## 卖家信用/评价

- 数据来源：商品详情页卖家区块（好评率、卖出件数、信用等级）+ 卖家主页「信用及评价」标签（好评数、评价标签统计）
- 缓存：每个卖家 7 天内只抓一次，避免频繁请求
- 风险分级：好评率 ≥98% 或"信用极好"→ 低；≥90% → 中；否则高；数据不足 → 未知
- 策略：风险只出现在通知和页面徽标中（绿/黄/红），**不会拦截通知**

## 视觉模型配置

阶段一「需求匹配」使用现有 LLM 配置（DeepSeek 即可）。阶段二「品相分析」需要视觉模型，在「设置」页单独配置：

- 通义千问：Base URL `https://dashscope.aliyuncs.com/compatible-mode/v1`，模型 `qwen-vl-max`
- 智谱：Base URL `https://open.bigmodel.cn/api/paas/v4`，模型 `glm-4v-flash`（免费）
- 硅基流动：Base URL `https://api.siliconflow.cn/v1`，模型 `Qwen/Qwen2.5-VL-72B-Instruct`

未配置视觉模型时，品相分析会被跳过并在通知中注明，需求匹配不受影响。

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
| `VISION_BASE_URL` / `VISION_API_KEY` / `VISION_MODEL` | 阶段二视觉模型（如通义千问 qwen-vl-max、智谱 glm-4v-flash）；不填则跳过品相分析 |
| `WECOM_CORPID` / `WECOM_AGENTID` / `WECOM_SECRET` / `WECOM_TOUSER` | 企业微信应用消息推送（免费，可推到微信） |
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
