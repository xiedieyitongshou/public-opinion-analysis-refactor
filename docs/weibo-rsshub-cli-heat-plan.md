# 微博 RSSHub 与 CLI 热度补强计划

本文定义如何使用 RSSHub 获取微博热搜事件种子，并结合微博 CLI 可获取数据，低成本分析微博相关话题热度。

## 背景结论

微博侧采用 RSSHub + 微博 CLI 的低成本链路：

- RSSHub 负责低频获取热搜话题种子。
- 微博 CLI 负责对已知话题做小样本搜索、微博详情、评论和转发补强。
- CLI 能力依赖开发者认证、登录状态和当前可用命令，必须可关闭、可降级。
- RSSHub 列表顺序和 CLI 搜索排序都不能解释为微博真实热度值。

因此，v0.3 不应把微博 CLI 当作稳定热榜主来源。更稳妥的策略是：

```text
RSSHub 负责微博热搜种子
微博 CLI 负责小样本关键词搜索、代表性微博详情、评论和转发补强
```

调研依据：

- 微博 CLI 服务页：`https://open.weibo.com/cli/plan`
- 微博 CLI npm 包：`@weibo-ai/weibo-cli`

## 目标

本计划只解决低成本微博侧热度补强，不解决全量微博舆情监控。

目标：

- 使用 RSSHub `/weibo/search/hot` 获取微博热搜话题候选。
- 跳过热榜第 1 条，取 Top N 作为事件种子。
- 使用微博 CLI 在有限额度内查询相关微博内容和互动信息。
- 将微博 CLI 结果映射为 `NormalizedItem` 和 `SourceSignal` 的可选补强字段。
- 为 `community_heat_score` 提供更可解释的微博侧弱信号。
- 明确缺字段、低额度、认证缺失和 CLI 不可用时的降级方式。

非目标：

- 不做微博全量采集。
- 不做大规模评论抓取。
- 不使用个人账号 cookie、代理池或反爬绕过。
- 不宣称“全网热度”或“微博真实总热度”。
- 不把 RSSHub 派生序号写成平台结构化 rank 或热度指标。

## 来源分工

| 来源 | 角色 | 主要字段 | 评分用途 | 状态建议 |
|---|---|---|---|---|
| RSSHub 微博热搜 | 事件种子 | `title`、`link`、列表序号 `list_position`、`fetched_at` | 话题出现信号，不单独代表热度 | `experimental_fallback` |
| 微博 CLI `search/statuses/limited` | 微博内容补强 | 微博正文、发布时间、微博 ID、用户、互动字段、搜索结果数 | 微博话题相关帖子样本和热度近似 | `fallback_pending_credentials` |
| 微博 CLI 评论接口 | 代表性微博补强 | 评论列表、评论时间、评论总数、用户反馈 | 讨论质量和反馈样本 | `fallback_pending_credentials` |
| 微博 CLI 转发接口 | 传播补强 | 转发列表、转发总数、转发时间 | 传播强度样本 | `fallback_pending_credentials` |

## 采集链路

### 1. 获取微博热搜种子

使用 RSSHub：

```text
/weibo/search/hot
```

处理规则：

- 每轮取 5-20 条。
- 默认跳过列表第 1 条，因为第 1 条可能是置顶 / 宣传位。
- `list_position` 只由 RSS item 顺序派生，更接近列表序号或展示顺序，不等于平台结构化 rank，也不等于热度值。
- 保存 RSSHub 原始 `title`、`link`、`guid`、`description` 和 `fetched_at`。
- 如果 RSSHub 连续失败，跳过微博种子，不阻塞其他已验证数据源。

输出：

```json
{
  "source": "weibo_rsshub_hot_search",
  "topic": "string",
  "list_position": 2,
  "topic_url": "string",
  "fetched_at": "datetime",
  "quality_flags": ["list_position_derived_from_rss_order", "not_heat_metric"]
}
```

### 2. 使用微博 CLI 做话题补强

优先使用体验服务白名单接口：

```bash
weibo-cli search statuses/limited --q "<topic>" --type 1 --count 10 --output json
```

如果命令别名为 `weibo`，可使用：

```bash
weibo search statuses/limited --q "<topic>" --type 1 --count 10 --output json
```

调用预算：

- 免费体验阶段：每轮最多 3-5 个微博话题。
- 每个话题只调用 1 次搜索。
- 每个话题只保留 Top 3-5 条相关微博。
- 只对最相关的 1-2 条微博调用评论或转发补强。
- 每天 smoke test 不超过体验白名单额度。

正式积分阶段：

- 只有在 69 元入门包可接受且单接口 Credit 单价明确后，才扩大采样。
- 仍然保持低频，不做全量抓取。
- 所有调用必须记录 Credit 消耗和失败原因。

### 3. 评论和转发补强

对搜索结果中的代表性微博 ID 执行小样本补强。

候选命令需要以实际 `weibo-cli commands show` 结果为准：

```bash
weibo-cli comments show/all --id "<weibo_id>" --output json
weibo-cli comments timeline/other --id "<weibo_id>" --count 20 --output json
weibo-cli statuses repost_timeline/all --id "<weibo_id>" --count 20 --output json
```

补强规则：

- 只补强搜索结果中相关性最高且互动最高的微博。
- 评论列表只采样第一页。
- 转发列表只采样第一页。
- 评论和转发不作为事件发现入口，只作为微博讨论 / 传播补充证据。
- 如果评论或转发接口不可用，不影响事件候选保留。

## 相关性过滤

微博 CLI 搜索结果必须经过相关性过滤，避免关键词污染。

保留条件至少满足一项：

- 微博正文强包含完整热搜标题。
- 微博正文包含核心实体和关键动作词。
- 话题 hashtag 与 RSSHub 热搜标题高度一致。
- 与新闻源标题共享核心实体和事件对象。

拒绝条件：

- 只命中泛词，不命中核心实体。
- 明显是历史事件或同名无关事件。
- 营销、抽奖、广告、搬运号内容占主导。
- 微博发布时间明显早于当前事件窗口。
- 搜索结果主要来自同一账号且缺少自然讨论迹象。

相关性输出：

```json
{
  "matched": true,
  "match_reason": "entity_and_action_overlap",
  "confidence": 0.82,
  "quality_flags": []
}
```

## 字段映射

### RSSHub 热搜种子

映射到 `SourceSignal`：

```text
platform = weibo
source_origin = rsshub
signal_role = topic_seed
topic_present = true
list_position = item_order
hot_value = null
listed_at = null
```

如果既有 schema 暂时仍使用 `derived_rank` 字段，该字段只能作为向后兼容名称处理，实际含义必须是 `list_position`。任何评分逻辑都不能把它解释为官方 rank、热度值或热度强弱的直接证据。

### 微博 CLI 搜索结果

映射到 `NormalizedItem`：

```text
platform = weibo
source_origin = weibo_cli
source_status = fallback_pending_credentials
external_id = status.id
mid = status.mid optional
title = text 前 40-80 字
content = text
url = status url optional
author = user.screen_name optional
published_at = created_at optional
fetched_at = collector time
raw_metrics = {
  repost_count,
  comment_count,
  like_count,
  total_number,
  search_sort,
  result_rank
}
```

字段注意：

- `total_number` 可作为 `mention_count` / `post_count` 的粗略代理，但必须标记为 `search_result_total_proxy`。
- 搜索返回顺序不是官方热搜排名。
- `sort=hot` 的结果顺序只能表示搜索接口内部排序，不等于热榜 rank。
- 如果互动字段缺失，不能补造点赞、评论、转发。

### 微博 CLI 评论补强

映射到 `SourceSignal` 或 item raw metrics：

```text
comment_sample_count
comment_total_number optional
comment_latest_at optional
comment_sentiment_sample optional
comment_quality_flags
```

评论文本只作为短期样本，不长期保存大规模原文。

### 微博 CLI 转发补强

映射到 `SourceSignal`：

```text
repost_sample_count
repost_total_number optional
repost_latest_at optional
repost_velocity_proxy optional
```

## 热度分析方法

微博侧不单独给出绝对热度，只计算可解释的弱微博分。

### 输入特征

RSSHub 特征：

```text
topic_present
list_position
snapshot_appearance_count
list_position_delta
```

微博 CLI 搜索特征：

```text
weibo_search_total_number_proxy
matched_status_count
top_status_comment_count
top_status_repost_count
top_status_like_count
latest_status_created_at
status_result_diversity
```

微博 CLI 评论 / 转发特征：

```text
comment_total_number
comment_sample_count
repost_total_number
repost_sample_count
comment_latest_at
repost_latest_at
```

### 微博弱热度分

建议拆成三个子分：

```text
weibo_topic_presence_score
weibo_content_activity_score
weibo_interaction_sample_score
```

计算原则：

- `weibo_topic_presence_score` 来自 RSSHub 话题是否出现、连续出现次数和列表位置变化。
- RSSHub `list_position` 只作为展示顺序和弱趋势参考，不能按“序号越小热度越高”直接换算热度。
- `weibo_content_activity_score` 来自 CLI 搜索结果数代理、相关微博数量和发布时间新鲜度。
- `weibo_interaction_sample_score` 来自代表性微博的评论、转发、点赞样本。
- 三者都先做微博平台内归一化，再进入事件级评分。
- 如果只有 RSSHub，没有 CLI 补强，微博分置信度降低。
- 如果有 CLI 搜索但相关性低，CLI 搜索结果不参与评分。

示例：

```text
weibo_signal_score
= weibo_topic_presence_score * 0.25
+ weibo_content_activity_score * 0.35
+ weibo_interaction_sample_score * 0.40
```

这个权重降低 RSSHub 列表序号的影响，把微博 CLI 搜索和互动样本作为更主要的微博侧热度补强来源。

### 接入事件评分

微博 RSSHub + CLI 只输出微博平台内信号，不负责决定事件是否成立。

接入规则：

- `weibo_signal_score` 只进入微博平台分项。
- 事件级综合评分由 `calculate_event_scores` 读取所有已验证来源后统一计算。
- 微博源不可用时，只标记 `weibo_signal_status = unavailable`，不对其他平台事件做惩罚。
- 微博源只有 RSSHub 种子、缺少 CLI 补强时，事件可以保留，但微博分置信度降低。
- 微博 CLI 搜索无高相关结果时，不补造微博热度，只记录失败原因。

## CLI smoke test 计划

### 前置条件

- 完成微博开放平台开发者认证。
- 安装 `@weibo-ai/weibo-cli`。
- 完成 CLI OAuth 登录。
- 领取 7 天体验服务。
- 使用 `weibo-cli doctor` 检查认证和服务状态。
- 使用 `weibo-cli commands list --available` 获取当前可用命令。

### 测试命令

```bash
weibo-cli doctor
weibo-cli me --output json
weibo-cli commands list --available --output json
weibo-cli commands show search statuses/limited --output json
weibo-cli search statuses/limited --q "<topic>" --type 1 --count 10 --output json
```

如果正式服务已开通，再验证：

```bash
weibo-cli commands show search hot_word/biz --output json
weibo-cli search hot_word/biz --output json
```

### 样本规模

```text
RSSHub 热搜：跳过列表第 1 条，取列表第 2-11 条
微博 CLI 搜索：选 3 个话题，每个 1 次
微博 CLI 搜索结果：每个话题保留 Top 3
微博 CLI 评论补强：最多 2 条代表性微博
微博 CLI 转发补强：最多 2 条代表性微博
```

### 验收指标

| 指标 | 通过标准 |
|---|---:|
| CLI 登录可用 | `doctor` 通过 |
| 可用命令发现 | 能列出 `search/statuses/limited` |
| 搜索成功率 | 3 个话题至少 2 个成功 |
| 字段完整率 | `id`、`text`、`created_at` 至少 80% 完整 |
| 互动字段可用性 | 至少观察到评论 / 转发 / 点赞中任一类字段 |
| 相关性保留率 | 搜索结果高相关比例不低于 30% |
| 额度消耗可控 | 不超过体验白名单或预算 |
| 降级可用 | CLI 失败时其他已验证数据源继续运行 |

## source_status 决策

初始状态：

```text
weibo_rsshub_hot_search = experimental_fallback
weibo_cli_search_statuses_limited = fallback_pending_credentials
weibo_cli_comments = fallback_pending_credentials
weibo_cli_reposts = fallback_pending_credentials
```

升级条件：

- `weibo_cli_search_statuses_limited` 连续多次 smoke test 成功，字段稳定，额度可控，可升为 `fallback`。
- `weibo_cli_comments` 只在已知微博 ID 补强中稳定可用时升为 `fallback`。

降级条件：

- 认证或登录长期不可用。
- CLI 命令返回 401 / 权限不足。
- 体验额度耗尽。
- 搜索结果相关性长期过低。
- 返回字段无法支撑 `NormalizedItem`。

## 降级策略

| 失败场景 | 处理 |
|---|---|
| RSSHub 微博热搜失败 | 跳过微博种子，使用其他已验证数据源 |
| CLI 未登录 / 未认证 | 标记 `weibo_cli_status = unavailable_credentials` |
| CLI 体验额度耗尽 | 停止 CLI 调用，使用最近一次有效样本 |
| CLI 搜索无高相关结果 | 保留 RSSHub 话题，标记 `weibo_cli_no_relevant_status` |
| 评论接口失败 | 不补评论，不影响搜索结果入库 |
| 转发接口失败 | 不补转发，不影响搜索结果入库 |
| 热搜 CLI 命令不可用 | 继续使用 RSSHub 热搜，不阻塞主流程 |

`score_detail_json` 必须记录：

```json
{
  "weibo_signal_status": "rsshub_only|rsshub_plus_cli|cli_unavailable|stale_or_unavailable",
  "weibo_list_position_source": "rss_item_order",
  "weibo_cli_budget_status": "trial|formal|depleted|not_configured",
  "weibo_cli_quality_flags": []
}
```

## 合规与公开边界

可以提交到公开仓库：

- CLI 调研结论。
- source_status 决策。
- 字段映射规则。
- 低频 smoke test 计划。
- 脱敏后的样本结构。
- mock 数据和测试用例。

不提交到公开仓库：

- 微博账号信息。
- OAuth token。
- CLI 本地凭证。
- 真实评论原文大样本。
- 未脱敏的用户信息。
- 任何绕过平台限制的脚本。

## 实施步骤

### Step 1：保留 RSSHub 事件种子

- 继续使用 `/weibo/search/hot`。
- 跳过列表第 1 条。
- 保存 Top N 话题快照。
- 标记 `list_position_derived_from_rss_order` 和 `not_heat_metric`。

### Step 2：执行 CLI 小样本搜索

- 用 `search/statuses/limited` 搜索 3 个话题。
- 保存搜索结果字段完整率。
- 记录相关性保留率和拒绝原因。

### Step 3：执行代表性微博补强

- 选每个话题最高相关微博。
- 尝试评论和转发补强。
- 记录接口是否可用、字段是否稳定、额度消耗。

### Step 4：形成微博弱热度信号

- 计算 `weibo_topic_presence_score`。
- 计算 `weibo_content_activity_score`。
- 计算 `weibo_interaction_sample_score`。
- 保存 `weibo_signal_status` 和 `quality_flags`。

### Step 5：接入事件评分

- 微博 RSSHub + CLI 只提供微博平台内信号。
- 不把微博缺失作为其他平台事件的负面信号。
- 输出事件级 `score_detail_json`。

## 最小实现版本

v0.3 的最小实现已经收敛为两层：

```text
RSSHub `/weibo/search/hot`：默认启用，获取微博热搜话题种子。
微博 CLI `search/statuses/limited`：默认关闭，只在认证、服务和额度可用时做小样本补强。
```

本地运行 RSSHub-only：

```powershell
docker run -d --name rsshub -p 1200:1200 diygod/rsshub
cd backend
python scripts/weibo_heat_minimal.py --base-url http://localhost:1200 --json
```

本地运行 CLI 补强：

```powershell
npm install -g @weibo-ai/weibo-cli@0.9.1
weibo-cli auth login
weibo-cli doctor
weibo-cli commands list --available --output json
python scripts/weibo_heat_minimal.py --base-url http://localhost:1200 --with-cli --cli-topic-limit 3 --json
```

Docker 运行 RSSHub-only：

```powershell
docker compose -f docker-compose.weibo.yml up -d rsshub
docker compose -f docker-compose.weibo.yml build weibo-heat
docker compose -f docker-compose.weibo.yml run --rm weibo-heat python scripts/weibo_heat_minimal.py --json
```

Docker 运行 CLI 补强：

```powershell
docker compose -f docker-compose.weibo.yml run --rm weibo-heat weibo-cli auth login --device
docker compose -f docker-compose.weibo.yml run --rm -e WEIBO_CLI_ENABLED=true weibo-heat python scripts/weibo_heat_minimal.py --with-cli --json
```

无人值守 Docker / CI 不运行登录流程，只通过环境变量注入：

```powershell
$env:WEIBO_CLI_TOKEN="<token>"
docker compose -f docker-compose.weibo.yml run --rm -e WEIBO_CLI_ENABLED=true weibo-heat python scripts/weibo_heat_minimal.py --with-cli --json
```

输出语义：

- `rsshub_only`：只获得 RSSHub 热搜种子。
- `rsshub_plus_cli`：获得 RSSHub 热搜种子，并成功取得微博 CLI 搜索样本。
- `cli_unavailable`：RSSHub 种子保留，但 CLI 未安装、未登录、无权限、额度不足或返回异常。

该版本不保存微博账号、OAuth token、cookie 或评论大样本；CLI 凭据只由微博 CLI 自身管理，或由运行环境以 `WEIBO_CLI_TOKEN` / `WEIBO_CLI_REFRESH_TOKEN` 注入。

## 最终建议

v0.3 默认实现采用以下微博侧链路：

```text
RSSHub 热搜种子
+ 微博 CLI 免费 / 低成本小样本补强
+ 明确降级和置信度标记
```

微博 CLI 只作为已知话题的搜索 / 评论 / 转发补强来源。RSSHub 继续负责话题种子，CLI 继续负责搜索 / 评论 / 转发补强。
