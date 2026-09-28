# 三模块真实数据联调记录：2026-09-28

本次完成一轮真实来源采集和完整分析编排，整体返回 `partial`。采集、归一化、事件抽取、评分、分类和趋势工具均执行到，但发现官媒数据陈旧与官媒支撑误匹配，业务结果尚不能视为验收通过。

可公开的[结构化实测汇总](live-analysis-validation-2026-09-28.summary.json)记录来源数量、字段完整率、任务状态和 CLI 复测结果，不包含原始正文、账号信息或凭据。

## 范围与环境

- 测试时间：北京时间 2026-09-28 19:26:05—19:26:38。
- 主链路入口：`python -m app.analysis_cli`，使用现有 `HotspotAnalysisAgent`、ToolRegistry 和业务实现；未替换为 mock 来源。
- 主链路来源：知乎热榜、微博 RSSHub、人民网、中国新闻网、新华网；每源请求上限 5，微博按配置跳过第 1 条，实际保留 4 条。
- 额外对本轮知乎热榜第 1 条执行 1 次真实搜索增强，返回上限 3。该请求单独验证现有搜索 Collector，未自动加入主链路，也未将其 3 条结果写入主链路数据库。
- 使用独立 SQLite 数据库，不写入日常应用数据库。原始结果与数据库位于 `notes/source-probe-raw/live-analysis-20260928-112421/`，受已有 `.gitignore` 规则保护。
- 抽取和匹配使用当前默认规则；未启用 LLM 或 embedding。采样间隔参数为 120 分钟，本次只运行一轮，未伪造第二轮观测。

| 检查项 | 实际结果 |
|---|---|
| 知乎密钥 | `backend/.env` 中存在 `ZHIHU_ACCESS_SECRET`，Settings 成功读取；真实额度、热榜和搜索请求均成功。未输出或复制密钥。 |
| 知乎客户端 | 项目现有 `ZhihuClient` 使用 httpx 调用官方 HTTP API，本次验证的是该客户端。 |
| 微博 CLI | PATH 中没有全局命令，但找到之前 npx 缓存的 `@weibo-ai/weibo-cli` 0.9.1，通过 Node 直接启动成功。 |
| CLI 登录/认证 | `doctor` 返回 `login=true`、`developer_verification=true`。 |
| CLI 服务 | `service=false`、`trial_expired`，余额为 0；真实搜索返回 `SERVICE_REQUIRED`。 |
| CLI 命令契约 | `commands show search statuses/limited` 能正常返回，仍要求 `q`、`type`，`count` 最小为 10。 |
| RSSHub | 启动本机已有 `diygod/rsshub:chromium-bundled` 镜像，真实 `/weibo/search/hot` 请求返回 HTTP 200。 |

测试通过子进程环境变量临时指定 CLI 命令并启用 1 个话题的补强，没有更改 `.env`。未安装新版本 CLI、领取服务或充值。

## 真实数据与第三周采样预期

| 来源 | 本次返回 | 字段与新鲜度 | 判断 |
|---|---:|---|---|
| 知乎热榜 | 5 | 标题和 URL 为 5/5，非空摘要 4/5；仍无热度值和发布时间；排名由列表顺序派生。 | 核心字段与原采样结论相符，摘要不能假定总是存在。 |
| 知乎搜索（额外请求） | 3 | 评论数、赞同数、排序分、编辑时间、权威等级均 3/3 可用；现有相关性规则均保留。 | 客户端和搜索 Collector 当前可用；仅是一个 query 的小样本。 |
| 微博 RSSHub | 4 | 标题、URL、列表序号可用；发布时间和平台热度值缺失。 | 仍适合作为话题种子，字段符合原定位。 |
| 微博 CLI 搜索 | 0 | 实际执行 1 次，退出码 1，错误码 `SERVICE_REQUIRED`。 | 登录有效，体验服务已到期，当前无法取得正文和互动补强。 |
| 人民网 RSS | 5 | 5 条发布时间均为 **2025-06-05**，均带 `stale_feed_candidate`。 | 接口能返回数据，但本次内容不满足当前新闻要求。 |
| 中国新闻网 RSS | 5 | 发布时间均为 **2026-09-28**，本轮抓取前数分钟；标题、URL、摘要、发布时间可用。 | 本次可作为当前官媒证据来源。 |
| 新华网 RSS | 5 | 发布时间字段均缺失，5 条链接路径均指向 **2022-12-14**。 | 当前入口返回旧内容，不能仅凭抓取成功作为实时 fallback。 |

主链路共 24 条，额外知乎搜索 3 条。表中的“可用”只描述本次请求，不代表持续稳定性。

知乎 quota 在测试前后均报告热榜剩余 100、搜索剩余 5000，没有反映本次请求的即时扣减。不能据此断言没有额度消耗；本次实际发送了 1 次热榜、1 次搜索，以及额度查询。

## 主链路执行结果

| 阶段 | 结果 |
|---|---|
| 采集 | 5 个主来源均返回 `succeeded`，共 24 条。微博 CLI 补强失败记录在质量标记中，RSSHub 条目得到保留。 |
| 归一化与接口适配 | 24 条归一化成功，保存 24 个 Item，生成 24 个 SourceSignal。 |
| 事件抽取与归并 | 抽取 24 个事件信号；创建 20 个事件，自动合并 0 个；4 个微博弱信号进入人工复核。 |
| 评分 | 保存 5 条知乎平台分数；写入 40 条事件/平台观测，包括未出现在榜单中的观测。40 条观测不代表 40 轮采集。 |
| 分类 | 返回 20 个分类：15 个 `F_official_only`、5 个 `D_single_platform_with_official`。下文的误匹配使这些分类不能直接作为可信结果。 |
| 趋势 | 返回 20 个分析对象；事件级状态 5 个 `partial`、15 个 `unknown`。本次没有第二轮历史，不能验收上升/下降趋势。 |
| 任务与日志 | 9 个 AgentTask、9 个 AgentToolCall，所有步骤均执行到；归并任务为 `partial`，整轮为 `partial`，无执行异常。 |

4 个微博 Item 均未自动关联 Event，复核原因是 `event_merge.weak_signal_only`。因此本轮没有验证成功的微博互动评分，也没有验证成功的跨平台事件合并。

## 实测暴露的问题

### 1. 陈旧官媒内容仍进入当前事件池

人民网 5 条旧新闻和新华网 5 条旧链接全部创建了 Event，并出现在本轮分类中。人民网已有 `stale_feed_candidate` 标记，但没有阻止自动建事件。

当前适配层依据采集时间保存条目并召回本轮官媒证据；`classify_events` 的证据池按 `Item.fetched_at` 过滤。新闻发布时间和新鲜度标记尚未形成进入当前候选池的有效约束。需要分别处理“今天抓到”和“今天发生/发布”，尤其是发布时间缺失的旧 RSS。

相关代码：`backend/app/services/analysis_interfaces.py` 的 `resolve_and_persist_events`、`classify_events`。

### 2. 官媒支撑存在可复现的无关内容误匹配

具体例子：知乎“常德老人养老金问题”被匹配到中国新闻网“中国高校应邀赴安哥拉开展专题讲座”的报道。

保存的匹配特征是：

```json
{
  "title_containment": false,
  "keyword_overlap": 0.0,
  "ngram_overlap": 0.0,
  "action_overlap": 1.0,
  "within_time_window": true
}
```

匹配结果仍为 `strong_official_match: action_overlap,within_time_window`，事件获得 `supported`。本轮 5 个知乎事件全部被标为有官媒支撑，不能将此理解为找到了 5 组正确证据。

代码核查发现：`official_support.py` 的 `_overlap_ratio` 在事件没有动作词时，从候选新闻自身提取动作词，再检查这些词是否存在于同一候选新闻，因而可产生 1.0 的自匹配。`_match_candidate` 又允许动作重合度单独构成强文本匹配。

还存在接口形状不一致：事件归并把特征写入 `event_detail_json.match_features`，官媒 matcher 的 `_event_data` 主要读取顶层 `entities/action_terms`。这也是后续必须对齐的数据接口，不能只调整分类阈值。

相关代码：`backend/app/services/official_support.py` 的 `_match_candidate`、`_event_data`、`_overlap_ratio`；真实样本复算证据保存为 `official-support-audit.json`。

### 3. CLI 服务到期的原因被归为普通命令失败

真实 CLI 返回了明确的 `SERVICE_REQUIRED`，当前 `classify_cli_failure` 将其归为 `weibo_cli_command_failed`。流程可以降级并继续，但后续应保留“服务未开通/已到期”的可操作原因。

## 验收结论与后续顺序

本次证明现有入口和三个模块能够处理真实响应并执行完整流程，知乎 API 客户端、RSSHub 解析和数据库交接可运行。未证明分类结果正确，也未完成微博 CLI 互动增强或多轮趋势验收。

优先处理官媒证据匹配和陈旧新闻入池，再使用本次保存的真实响应回放验证。微博 CLI 搜索需要有效服务后重测；趋势需要按实际间隔新增采集轮次。Day48 性能评估不在本次范围。

本轮没有修改业务代码或匹配阈值，保留了发现问题时的原始结果。临时 RSSHub 测试容器在测试结束后移除。

## 本地证据

目录：`notes/source-probe-raw/live-analysis-20260928-112421/`

- `invocation.json`：实际入口参数和临时微博配置。
- `collection.json`：真实主链路采集输出，可供离线回放。
- `analysis-output.json`、`stage-outputs.json`：完整分析结果和各 tool 输出。
- `analysis.db`：本轮独立数据库。
- `zhihu-search.json`：单独搜索增强结果。
- `weibo-cli-results.json`：真实 CLI 搜索失败响应。
- `official-support-audit.json`：基于本轮数据库复算的官媒匹配证据。
- `summary.json`：来源、字段、状态、数量与额度查询汇总。
- `probe.py`：本次本地执行脚本；再次运行会发起真实请求，回放应使用 `collection.json`。

## 后续复测：微博 CLI 服务已恢复

北京时间 2026-09-28 19:45 再次检查，`doctor` 返回 `ready=true`，登录、开发者认证和服务均通过，服务状态变为 `formal_active`。
通过项目的 `WeiboHeatClient.search_with_cli` 对同一个话题执行一次真实搜索，退出码为 0，`available=true`。
请求 `count=10`，客户端按本次配置保留 3 条样本；3 条样本的 ID、MID、正文、发布时间、转发数、评论数和点赞数均有值，质量标记为空。

这次复测确认 CLI 搜索与项目客户端解析可用。上文主链路记录仍保留首次测试时的服务到期结果；整轮分析尚未重新执行，官媒新鲜度和误匹配问题尚未修复。
复测证据：`notes/source-probe-raw/live-analysis-20260928-112421/weibo-cli-recheck-20260928-114522.json`。
