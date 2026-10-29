# 数据源与采集

[返回项目首页](../README.md) · [来源字段映射](source-fields.md) · [搜索增强边界](search-enrichment.md)

本文说明各来源 Collector 的职责、请求范围和失败处理。历史服务试用、访问探测及字段采样见[验证记录](../README.md#来源采样)。

## 来源分工

| 来源 ID | 获取路径 | 在业务中的作用 |
| --- | --- | --- |
| `zhihu_hot_list` | 知乎数据开放平台 API | 知乎热点发现、真实榜位和上榜观测 |
| `zhihu_search` | 知乎搜索 API | 已知知乎热榜问题的回答与互动增强 |
| `weibo_rsshub_hot_search` | RSSHub `/weibo/search/hot` | 微博话题种子与上榜观测；可内嵌 CLI 增强 |
| `people_politics_rss` | 人民网 RSS | 官方报道、官媒事件发现和证据 |
| `chinanews_scroll_rss` | 中新网 RSS | 官方报道、官媒事件发现和证据 |
| `xinhua_politics_rss` | 新华网 RSS | 补充官方报道；保留来源质量与时效限制 |

实际注册入口为 [`collectors/registry.py`](../backend/app/collectors/registry.py)。微博 CLI 是 RSSHub Collector 内的增强客户端，不是单独定时发现新话题的来源。官媒定向搜索位于分类服务，见[官媒证据](official-evidence.md)。

采用这些来源，是为了分别获得社区关注、讨论样本与官方报道，保持获取路径和指标可解释。B 站、抖音、小红书、微信全文、微博评论／转发递归采集等尚未接入正式主链路。

## 知乎：热榜发现与同问题增强

知乎热榜条目提供问题标题、URL 和真实 `rank`；搜索结果提供回答、问题身份、时间和可用互动指标。二者字段不同，统一成 `NormalizedItem` 时保留差异，不补造缺失热度值。

MVP 日报入口先取热榜，再为最多三个本平台问题生成搜索候选，每个查询请求上限五条。这个限制属于知乎同平台增强；微博已采用不同的全话题覆盖配置。独立分析 CLI 是否触发知乎增强取决于所选来源和 `per_source_params`，不能把所有入口都理解为自动搜索。

凭据使用 `ZHIHU_ACCESS_SECRET`，保存在本地环境文件。单独验证凭据和协议可运行 `backend/scripts/zhihu_smoke_test.py`；这是真实外部调用，不是离线测试。

## 微博：RSSHub 与 CLI

### 话题发现

RSSHub 负责获取热搜标题、URL 和列表顺序。`list_position` 是 RSS 中的位置，不是微博官方热度，也不能当作真实排名变化。`fulltext` 路由属于采样时验证过的摘要增强路径，不是正式互动热度来源。

当前基础配置请求 20 条并跳过首条，正常有 19 个有效话题。有效范围由本轮 `limit`、`skip_top` 和实际响应共同决定；请求上限不保证一定返回同样数量。

### 语义判断后端

采集协议与热度公式保持独立；可用 `EVENT_REASONING_MODE=rules|llm` 切换抽取、搜索相关性、事件归并和官媒补证的判断后端，见[DeepSeek 独立模式](llm-reasoning.md)。默认 rules，LLM 已做有限历史材料回放，尚未完成全量验收。微博长正文优先读取 `long_text.long_text_content`，缺失时回退短文本；历史简报不自动变化。

### CLI 全话题覆盖

CLI 开启后，对本轮每个入选 RSS 话题各执行一次 `search statuses/limited`，固定 `sort=hot`、默认 `count=10`。全部返回记录先做逐条相关性与冲突检查，再汇总已接纳样本的数量、最新时间和互动字段；不先截掉后五条。

2026-10-25 起由 `time` 改为 `hot`，用于优先获取平台筛选的热门微博正文；请求条数、接纳条数及现有热度公式暂时保留。官方 CLI 将 `hot` 定义为按热门度返回精选微博，未公开此接口的排序公式；它不代表全话题互动最高的完整榜单，也不包含评论区正文。原有相关性、冲突和 72 小时时效过滤继续生效，不能仅因结果属于热门就直接接纳。排序进入采样签名，历史 `time` 观测不与 `hot` 观测直接计算趋势。

| 配置 | 代码默认值 | 含义 |
| --- | --- | --- |
| `WEIBO_CLI_ENABLED` | `false` | 基础配置关闭；Docker 部署模板设为 `true` |
| `WEIBO_RSSHUB_FETCH_LIMIT` | `20` | RSS 客户端默认上限；调用入口可指定本轮 limit，最大 50 |
| `WEIBO_RSSHUB_SKIP_TOP` | `1` | 默认跳过首条 |
| `WEIBO_CLI_TOPIC_LIMIT` | `50` | 增强覆盖上限，实际只搜索入选话题，不固定调用 50 次 |
| `WEIBO_CLI_SEARCH_COUNT` | `10` | 实际请求至少 10；兼容旧配置 5，但不再截断为 5 条 |
| `REQUEST_LIMITS.weibo_cli` | `50` | MVP 任务中每轮 CLI 调用预算 |

以 20 条 RSS、跳过首条为例，正常是 19 次 CLI 搜索，每次请求 10 条；返回量和过滤后有效量可能更少。旧环境文件里显式设置的 3 话题或 3 次预算不会被新默认值覆盖，升级时需同步调整。

历史文档中的“3–5 话题”来自免费体验和 smoke test，不能作为当前正式监测默认值。是否有可用服务、认证和余额仍由外部服务决定；项目不根据请求次数虚构账户余额或费用。

### 指标与失败处理

- 只有已接纳样本进入 `matched_status_count` 和转评赞汇总；逐条关系保存在 `raw_payload.cli_enrichment.samples[].relation`。
- 查询总命中 `total_number_proxy` 包含未返回、无法核验的结果，当前只存 `normalized.cli_query_total_number_proxy` 供审计，不进入正式热度输入。
- 认证、超时、额度或单话题失败不删除 RSS 话题，也不阻断其他话题；记录具体缺失原因。
- RSS-only 可给话题出现、有限静态分和连续观测，无法提供可靠的互动升降温判断。
- 请求条数、排序、有效范围和处理版本进入采样签名，变更配置后要积累同口径的新观测，不能直接拿旧样本算趋势。

实现见 [`weibo_heat_client.py`](../backend/app/services/weibo_heat_client.py) 与 [`collectors/weibo.py`](../backend/app/collectors/weibo.py)；评分与趋势见[平台热度](platform-heat.md)。

### 独立调试

从 `backend` 目录使用已安装依赖的环境：

```powershell
docker compose -f ..\docker-compose.weibo.yml up -d rsshub
..\.venv\Scripts\python.exe scripts/weibo_heat_minimal.py --base-url http://localhost:1200 --json
```

CLI 需安装项目使用的 `@weibo-ai/weibo-cli@0.9.1` 并配置授权；可以用 `weibo-cli doctor` 检查。确认服务可用后，调试命令加 `--with-cli` 会发起真实搜索。完整 Docker 镜像已安装 CLI，服务器授权另行配置，见[部署指南](deployment.md)。

## 官媒：报道发现与社区补证

官媒 RSS 条目进入相同的归一化、抽取和事件构建链路。只有滚动 24 小时内、发布时间可靠的报道作为当天发现；旧记录继续保存审计。

一个官媒事件可以独立形成 F 类，也可以通过事件融合与社区事件关联。分类时还可对未获支持的社区事件进行限额内的官媒查询，查询结果仍须二次判定。报道数量和媒体覆盖只说明证据来源，不转成社区热度。

## 状态、完整性与预算

`source_status` 表示来源选型／质量层级（如 `use`、`fallback`），本轮是否采集成功看结果 `status`。`list_complete` 表示当前选定范围是否可用于判断缺席，不表示覆盖整个平台。

成功取得合法短榜或空榜时，可以在当前范围内确认离榜。失败、无效结构、已知数量缺失、身份字段缺失或采样范围不可比时，保留未知。详细时间语义见[入榜与离榜](platform-heat.md#滚动窗口与入榜离榜)。

MVP 任务通过 `REQUEST_LIMITS` 限制每轮每源调用，重试也计数。后台区分 HTTP／CLI 实际请求与 Tool 次数；RSSHub 容器内部对微博的请求不在后端请求统计中。

来源原始响应、密钥和私人采样不进入公开报告。当前配置以 [`config.py`](../backend/app/core/config.py)、[本地模板](../backend/.env.example)、[部署模板](../deploy/.env.example)为准；单次历史采样不代表长期可用率。
