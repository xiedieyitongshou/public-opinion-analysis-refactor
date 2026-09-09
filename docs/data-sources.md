# 数据源设计

## 文档目标

本文定义舆情观察系统 v0.3 第一阶段的数据源范围、选型标准、字段价值和合规边界。

第一阶段目标不是覆盖尽可能多的平台，而是选择少量稳定、可解释、可标准化的数据源，支撑“一图流热点简报”的完整链路：

```text
数据采集
-> 标准化内容
-> 事件抽取与合并
-> 热度评分
-> 走势记录
-> 来源证据
-> 简报生成
```

## 选型原则

第一阶段数据源按以下维度评估：

- 数据标准化：是否容易稳定获得标题、链接、发布时间、栏目、摘要、基础指标。
- 获取难易度：是否存在公开列表页、RSS、稳定栏目页或其他适合公开项目展示的入口。
- 字段价值：字段是否能支持话题提取、事件合并、热度评分和来源引用。
- 事件发现价值：是否能稳定提供可进入统一事件候选池的标题、话题或问题。
- 权威性：是否适合作为事实确认、权威报道、议程设置和来源可信度判断依据。
- 信息准确性：内容适合承担事实依据、话题线索、关注点提取、传播信号中的哪一种角色。
- 热度贡献：该来源更适合贡献关注度、传播速度、报道强度、证据强度中的哪一类分数。
- 稳定性：页面结构、更新频率和字段格式是否相对稳定。
- 合规边界：是否依赖敏感账号、Cookie、私有接口或高风险采集方式。

第一阶段优先接入低风险、低复杂度、高字段价值的数据源。高反爬、高登录依赖、高个性化推荐的平台暂不作为真实采集目标。

当前已完成第 2 周 Agent Runtime 和 Tool Runtime，后续计划不回滚已完成工程骨架。从第 3 周开始，数据源可行性测试作为正式 Collector 开发前的硬闸门：

```text
source_status = use      # 稳定公开入口已验证，可进入第 4 周 collector
source_status = fallback # 可访问但字段或稳定性有缺口，可作为补充源
source_status = postpone # 未确认稳定公开入口，不进入正式 collector
```

第 4 周只实现 `use` 或经过明确降级设计的 `fallback` 来源。`postpone` 来源可以保留在产品设计和 mock 链路中，但不能作为第一阶段真实采集的硬依赖。

对于微博和知乎，第一阶段不采用个人账号 cookie、自动登录、代理池或绕过风控方式作为稳定数据路径。微博侧收敛为 RSSHub 热搜话题种子 + 微博 CLI 低频搜索补强；知乎侧使用已验证的数据平台接口。

当前 Day 15 初步探测状态：

| 来源 | 当前状态 | 工程判断 | 说明 |
|---|---|---|---|
| 人民网 | `use` | 可进入第 4 周 collector | RSS 可访问，标题、链接、发布时间、摘要等字段较完整 |
| 中国新闻网 | `use` | 可进入第 4 周 collector | RSS 可访问，字段较完整；需要 XML 容错解析 |
| 新华网 | `fallback` | 可做降级 collector 或继续寻找更好入口 | RSS 可访问，标题和链接稳定；当前入口发布时间缺失 |
| 微博热搜 | `fallback_candidate` | RSSHub 提供实验性话题种子，微博 CLI 对已知话题做搜索 / 互动补强 | 直连公开页触发访客 / 登录校验；不使用账号 cookie 或反爬绕过 |
| 知乎热榜 | `use` | 可进入第 4 周 collector | 知乎数据开放平台 `hot_list` Access Secret smoke test 已通过；直连公开页仍不使用 |

## 数据源分层

系统将数据源分为两类：

- 官媒 / 新闻源：通常具有较高证据强度、报道强度和权威议程价值。
- BBS / 社区源：通常具有较高即时关注、传播速度、话题发现和公众问题价值。

两类来源在系统中的角色不同，但不能简单理解为“官媒是信息源头、社区是讨论源”。娱乐、消费、游戏、科技产品、小众行业和地方话题经常由社区先出现，官媒可能不报道、延后报道或只在有限范围报道。因此，话题提取阶段不按来源类型设置优先级：

```text
所有 NormalizedItem
-> 统一提取 EventSignal
-> 进入同一个 EventCandidate 池
-> 按实体、时间、语义和来源证据进行事件合并
```

来源类型的差异主要体现在热度计算、证据置信度和输出措辞，而不是体现在“能不能生成事件候选”。

第一阶段采用“统一事件池 + 分源评分贡献”：

```text
事件提取：官媒和 BBS 都可以触发 EventCandidate
事件合并：不按来源类型优先，按标题、实体、时间窗口和语义相似度判断
热度计算：官媒和 BBS 的贡献项不同，权重不同
事实表达：按来源可信度、证据数量和 guardrails 决定措辞强弱
```

系统不应把单一社区来源写成确定事实，也不应把官媒报道简单等同于事件全貌。社区先出现的话题可以进入事件池，但在缺少足够证据时只能表达为“社区关注/讨论正在升温”；官媒报道可以提升证据强度和权威报道强度，但不能直接推出“公众高度关注”。

## 标准化目标字段

第一阶段所有数据源统一映射为 `NormalizedItem`。

建议字段如下：

| 字段 | 必填 | 说明 |
|---|---|---|
| `source_id` | 是 | 数据源 ID |
| `source_name` | 是 | 数据源名称 |
| `source_type` | 是 | `official_news` / `community_hotlist` / `community_question_hotlist` |
| `source_status` | 是 | `use` / `fallback` / `postpone`，由第 3 周 smoke test 决定 |
| `platform` | 是 | 平台标识 |
| `channel` | 否 | 栏目或分区 |
| `rank` | 否 | 榜单排名 |
| `title` | 是 | 标题、话题名或问题标题 |
| `url` | 是 | 来源链接 |
| `published_at` | 否 | 发布时间，无法获取时标记缺失 |
| `fetched_at` | 是 | 采集时间 |
| `author` | 否 | 作者、媒体、UP 主等 |
| `summary` | 否 | 摘要或列表页描述 |
| `content_text` | 否 | 正文，第一阶段不强制 |
| `raw_metrics` | 否 | 原始热度指标 JSON |
| `raw_payload` | 否 | 原始字段 JSON，用于调试和追溯 |
| `quality_flags` | 否 | 字段缺失、异常或低置信度标记 |
| `signal_role` | 否 | `event_signal` / `attention_signal` / `evidence_signal` / `mixed_signal` |
| `score_contribution_role` | 否 | `attention` / `evidence` / `authority` / `velocity` / `coverage` |

第一阶段只要求稳定获得：

```text
source_id
source_name
source_type
source_status
platform
title
url
fetched_at
```

以下字段允许缺失：

```text
published_at
channel
rank
author
summary
content_text
raw_metrics
```

所有来源进入事件提取时都应先转成同一类 `EventSignal`。`source_type` 不决定是否进入事件候选池，只决定后续评分和 guardrail：

```text
NormalizedItem
-> EventSignal
-> EventCandidate
-> Event
```

建议 `EventSignal` 至少包含：

```text
title
source_id
source_type
source_status
platform
url
published_at optional
fetched_at
rank optional
raw_metrics optional
signal_role
confidence_score
quality_flags
```

## 官媒 / 新闻源

### 第一阶段接入

第一阶段建议接入以下新闻源：

```text
人民网
中国新闻网
新华网
央视网
```

这组来源覆盖权威新闻、即时新闻、重大事件报道和视频新闻入口。它们和 BBS 源一样进入统一事件候选池，但在热度编排中主要贡献 `evidence_score`、`authority_score` 和 `coverage_score`。

### 官媒评估表

| 来源 | 获取难易度 | 标准化难度 | 事件发现价值 | 热度贡献重点 | 证据贡献 | 第一阶段决策 |
|---|---|---|---|---|---|---|
| 人民网 | 低 | 低 | 中 | authority / coverage | 高 | 优先接入 |
| 中国新闻网 | 低 | 低 | 高 | coverage / velocity | 中高 | 优先接入 |
| 新华网 | 中 | 中 | 中 | authority / evidence | 很高 | 优先接入 |
| 央视网 | 中 | 中高 | 中 | authority / coverage | 高 | 浅接入 |

### 人民网

人民网适合作为第一阶段标准化样板。

优势：

- 权威性高。
- 栏目清晰。
- 标题、链接、栏目等字段容易统一。
- 适合作为时政、社会、法治等事件的候选输入、事实确认、政策背景和权威议程来源。

第一阶段建议字段：

```text
title
url
channel
published_at
summary optional
content_text optional
```

接入策略：

- 优先接入公开列表页或 RSS。
- 先采集标题、链接、栏目、发布时间。
- 正文解析作为增强能力，不作为第一阶段硬依赖。

### 中国新闻网

中国新闻网适合作为第一阶段即时新闻和多栏目覆盖来源。

优势：

- 栏目覆盖广。
- RSS 和列表结构相对友好。
- 适合补充社会、国际、财经、文娱、体育等多领域事件线索。
- 标准化成本低。

第一阶段建议字段：

```text
title
url
channel
published_at
summary optional
content_text optional
```

接入策略：

- 优先作为第一个采集器验证对象。
- 用它验证 `fetch_source_items`、`normalize_raw_item` 和入库去重流程。
- 正文不稳定时，只保留列表页摘要和来源链接。

### 新华网

新华网适合作为重大事件确认和权威报道链路来源。

优势：

- 权威性很高。
- 对重大国内外事件具有较强事实确认价值。
- 适合提升事件来源可信度和官方报道强度评分。
- 适合观察重大事件是否进入更高权威报道链路。

风险：

- 不同频道页面结构可能存在差异。
- 部分字段需要按栏目单独适配。

第一阶段建议字段：

```text
title
url
channel
published_at optional
summary optional
content_text optional
```

接入策略：

- 先接入新闻列表和重点栏目。
- 不把全文解析作为第一阶段阻塞项。
- 用于增强事件证据强度、权威来源覆盖和报道链路判断。

### 央视网

央视网适合作为视频新闻和中央媒体报道来源，但第一阶段只建议浅接入。

优势：

- 权威性很高。
- 适合重大公共事件、新闻联播、专题报道等来源引用。
- 能补充视频新闻维度。

风险：

- 内容形态包含视频、节目、专题页，结构比纯新闻列表复杂。
- 视频内容不适合第一阶段深度解析。

第一阶段建议字段：

```text
title
url
channel
published_at optional
summary optional
content_text optional
raw_payload optional
```

接入策略：

- 只采集新闻频道、重点栏目或列表页的标题与链接。
- 不解析视频正文、字幕、评论或节目全文。
- 作为事实确认、权威报道和议程信号，不作为复杂多媒体分析对象。

## BBS / 社区源

### 第一阶段接入

第一阶段建议验证以下社区候选源：

```text
微博热搜
知乎热榜
```

这组来源都以文字标题、话题或问题为主要输入，分别覆盖即时热点和问题讨论。微博侧采用 RSSHub 话题种子 + CLI 搜索补强，知乎侧采用已验证的数据平台接口。未通过 smoke test 的路径只保留为 mock 或后续候选。第一阶段先不接入视频社区源，避免在 MVP 阶段引入视频标题语义偏差、播放指标归一化和多媒体内容解析成本。

### 社区源评估表

| 来源 | 获取难易度 | 标准化难度 | 事件发现价值 | 热度贡献重点 | 稳定/合规风险 | 第一阶段决策 |
|---|---|---|---|---|---|---|
| 微博热搜 | 中 | 低 | 很高 | attention / velocity | 中高 | 优先验证 |
| 知乎热榜 | 中高 | 中 | 中高 | discussion / attention | 中高 | 优先验证 |
| B站热门 / 排行榜 | 中 | 中 | 高 | attention / velocity | 中 | 后续候选，第一阶段暂不接入 |
| 小红书 | 高 | 高 | 高 | attention / community | 高 | 暂不接入 |
| 抖音 | 高 | 高 | 高 | attention / velocity | 高 | 暂不接入 |
| 微信公众号全文 | 高 | 中高 | 中 | evidence / coverage | 高 | 暂不接入 |
| 豆瓣小组深度采集 | 高 | 中高 | 中 | discussion / niche | 高 | 暂不接入 |

### 微博热搜

微博热搜是第一阶段最有价值的社区热点发现候选源，但当前不等于已具备稳定真实采集路径。

项目路径判断：

- RSSHub `/weibo/search/hot` 可作为实验性热搜话题种子入口，但列表顺序只能派生 `list_position`，不能解释为官方 rank 或真实热度值。
- 微博 CLI `search/statuses/limited` 可对已有话题做低频搜索补强，获取代表性微博正文、发布时间、微博 ID 和转评赞字段。
- 可选使用微博 CLI 评论 / 转发命令补充代表性微博样本，但不做大规模评论或传播链采集。
- CLI 搜索结果必须经过相关性过滤，低相关微博只进入审计日志。
- CLI 未登录、额度不足或命令不可用时，微博链路降级为 RSSHub-only 或跳过，不阻塞知乎和官媒链路。

优势：

- 热搜榜天然就是即时公共关注信号。
- 字段结构相对明确。
- 话题名、列表位置、搜索结果规模代理和代表性微博转评赞样本适合提供弱关注信号。
- 对社会、文娱、公共事件传播非常敏感。

风险：

- 热搜话题通常较短，语义不一定完整。
- 娱乐化和情绪化内容较多。
- 不能单独作为事实确认来源。
- 不适合第一阶段抓取评论、转发链路或用户主页。

第一阶段建议字段：

```text
list_position
title
url
fetched_at
status_id optional
created_at optional
comments_count optional
reposts_count optional
attitudes_count optional
total_number_proxy optional
```

系统角色：

```text
source_type = community_hotlist
signal_role = attention_signal
score_contribution_role = attention / velocity
source_origin = rsshub / weibo_cli
source_status = experimental_fallback / fallback_candidate
```

处理规则：

- 可以触发新事件候选和社区关注点候选。
- 不能单独生成确定事实。
- 需要结合新闻源、知乎或人工确认提升事实置信度。
- 即使不能确认事实，也可以作为“公众关注正在升温”的低置信信号保留。

### 知乎热榜

知乎热榜适合作为问题型讨论热点候选源。当前已验证知乎数据开放平台 `hot_list` API，可以作为第一阶段稳定数据源；公开热榜页仍不作为采集入口。

官方授权路径判断：

- 知乎数据开放平台可访问，页面元数据明确包含 `API`、`MCP`、`Skills`、`搜索`、`直答`、`知识库`、`RAG`、`热榜` 等能力关键词。
- 开发者站点前端存在 `/hotlist`、`/docs`、`/authentication` 等路由，以及 `/console/api/user_info`、`/console/api/v3/docs` 等控制台接口线索。
- 官方接口契约已确认：`/api/v1/content/hot_list`、`/api/v1/content/zhihu_search`、`/api/v1/quota`。
- `hot_list` Access Secret smoke test 已通过，成功返回 10 条样本。
- quota 已确认：`hot_list` 每日额度 100，`zhihu_search` 每日额度 5000。
- `hot_list` 不返回真实 `hot_value` 和 `published_at`，第一阶段使用榜单顺序生成 `rank`，用快照时间和排名变化计算热度与趋势。
- `zhihu_search` 已通过低频 smoke test，但定位为模式 B 指定事件补证工具，不作为模式 A 常规定时采集源。
- 不使用个人账号 cookie、浏览器自动登录、代理池或私有接口作为稳定采集路径。

优势：

- 问题标题通常比微博话题更完整。
- 适合捕捉社会、科技、财经、教育、职场等讨论。
- 有助于补充事件的公众关注角度。

风险：

- 页面和指标字段稳定性不如新闻 RSS。
- 回答内容质量不稳定。
- 观点、推测和事实容易混杂。
- 不适合第一阶段深抓回答和评论。

第一阶段建议字段：

```text
rank
title
url
heat_value optional
thumbnail_url optional
summary optional
answer_count optional
excerpt optional
fetched_at
```

系统角色：

```text
source_type = community_question_hotlist
signal_role = discussion_focus_signal
score_contribution_role = discussion / attention
source_origin = official_api
source_status = use
```

处理规则：

- 可以触发新事件候选。
- 对事实表述必须保守。
- 如果只来自知乎，输出中应标记为“讨论热度”而不是“事实确认”。
- 可用于提取公众关注角度、争议问题和解释型需求。

### B站热门 / 排行榜

B站适合作为视频社区传播信号，但不适合作为第一阶段事实来源。当前阶段社区源先收敛到微博和知乎这类文字型 BBS / 热榜源，因此 B站只保留为后续候选，不进入 v0.3 第一阶段 smoke test 和正式 Collector 范围。

优势：

- 视频播放、弹幕、点赞等指标能反映传播强度。
- 适合补充科技、文娱、知识、社会议题的视频传播维度。
- 与微博、知乎形成平台差异。

风险：

- 视频标题可能存在标题党、二创、梗、混剪等干扰。
- 标题与真实事件之间的语义距离可能较远。
- 视频内容理解、弹幕语义分析和评论分析成本高。
- 不适合作为事实依据。

第一阶段建议字段：

```text
rank
title
url
author
channel
view_count optional
danmaku_count optional
like_count optional
published_at optional
fetched_at
```

系统角色：

```text
source_type = community_video_hotlist
signal_role = attention_signal
score_contribution_role = attention / velocity
```

处理规则：

- 默认不单独触发确定事件。
- 默认不能单独生成事实表述。
- 与官媒、微博或知乎弱匹配时，不自动合并，进入候选或人工确认。
- 综合热度评分中默认降权。
- 只有标题包含明确实体、动作和事件对象时，才允许作为新事件候选。

第一阶段不做：

- 视频内容理解。
- 弹幕语义分析。
- 评论区抓取。
- UP 主画像。
- 标题党识别模型。

## 暂不接入来源

以下来源第一阶段暂不做真实采集：

```text
小红书
抖音
微信公众号全文
豆瓣小组深度采集
大规模评论抓取
高登录依赖平台
高个性化推荐流
```

暂不接入原因：

- 获取方式不稳定。
- 标准化成本高。
- 对登录态、推荐流或客户端环境依赖重。
- 公开项目展示存在合规风险。
- 字段含义不统一，容易误导热度评分。

这些来源可以作为后续版本候选，但不进入 v0.3 第一阶段真实采集范围。

## 字段价值矩阵

| 来源 | 标题 | 链接 | 发布时间 | 栏目/分区 | 排名 | 热度值 | 作者 | 摘要 | 正文 | 第一阶段价值 |
|---|---|---|---|---|---|---|---|---|---|---|
| 人民网 | 高 | 高 | 中高 | 高 | 无 | 无 | 中 | 中 | 中 | 权威议程与事实确认 |
| 中国新闻网 | 高 | 高 | 高 | 高 | 无 | 无 | 中 | 中 | 中 | 多领域新闻与事件线索 |
| 新华网 | 高 | 高 | 中 | 中 | 无 | 无 | 中 | 中 | 中 | 重大事件确认与权威链路 |
| 央视网 | 高 | 高 | 中 | 中 | 无 | 无 | 中 | 低 | 低 | 权威报道与视频新闻入口 |
| 微博热搜 | 高 | 高 | 低 | 中 | 高 | 高 | 无 | 低 | 无 | 即时关注和传播节奏 |
| 知乎热榜 | 高 | 高 | 低 | 中 | 高 | 中 | 无 | 中 | 低 | 讨论焦点和公众问题 |
| B站热门 | 高 | 高 | 中 | 高 | 高 | 中高 | 高 | 低 | 无 | 后续视频传播候选 |

## 接入优先级与第 3 周闸门

当前已完成第 2 周工程骨架，后续不回滚 Agent Runtime / Tool Runtime。第 3 周应优先解决数据源获取途径可行性，再进入第 4 周正式 Collector 开发。

第 3 周建议按以下顺序执行：

```text
Day 15：对 3 个官媒源和 2 个文字社区源做获取路径 smoke test
Day 16：知乎官方 API 授权 smoke test 已通过；微博收敛为 RSSHub 话题种子 + CLI 搜索补强
Day 17：基于 use / fallback 源修正 NormalizedItem 和 EventSignal
Day 18-20：基于真实字段修正 guardrails、scoring 和基础 API
Day 21：确认第 4 周正式 collector 清单
```

正式 Collector 的开发顺序由 `source_status` 决定，而不是由产品期望决定：

```text
use：进入第 4 周正式 collector
fallback：可做降级 collector 或补充源
postpone：不进入第 4 周正式 collector，只保留 mock 或候选说明
```

建议第一阶段分批接入。

第一批：

```text
中国新闻网
人民网
微博热搜
```

目标是验证：

- 新闻源采集。
- 社区热榜采集。
- `NormalizedItem` 标准化。
- 入库去重。
- 统一事件候选池。
- 基础热度分项评分。

第二批：

```text
新华网
知乎热榜
```

目标是增强：

- 重大事件权威来源。
- 问题型讨论信号。
- 事件合并质量。
- 新闻报道链路与社区话题焦点的交叉验证。

如果微博 RSSHub / CLI 或知乎数据平台在 Day 15-16 后仍不可用，第 4 周不应强行实现对应真实 collector。此时应保留 mock community source 验证事件池、评分和 guardrails。

后续候选：

```text
央视网
B站热门 / 排行榜
```

目标是补充：

- 视频新闻信号。
- 视频社区传播信号。
- 多平台展示差异。

B站和央视网都包含视频内容。央视网可作为官媒浅接入候选；B站不进入第一阶段社区源范围，后续如接入也只做浅层条目采集，不做视频语义理解。

## 热度编排与评分边界

不同平台的原始热度指标不能直接横向比较。

示例：

```text
微博 CLI 代表性微博互动样本
知乎搜索互动样本
新闻源报道数量
```

这些指标不是同一量纲，也不代表同一种“重要性”。第一阶段不应把官媒和 BBS 先后分层处理，而应在事件形成后分别计算贡献项：

```text
attention_score：社区关注度，主要来自微博、知乎等 BBS / 热榜源
velocity_score：短时间升温速度，来自排名变化、重复出现、更新时间间隔
discussion_score：问题讨论强度，主要来自知乎等问答 / 讨论型来源
evidence_score：证据强度，主要来自新闻源数量、来源可信度、可引用链接
authority_score：权威报道强度，主要来自人民网、新华网、央视网等权威源
coverage_score：跨来源覆盖度，来自不同 source_type / platform 的覆盖情况
```

综合排序使用 `total_priority_score`，但展示时必须保留分项解释：

```text
total_priority_score
= attention_score * attention_weight
+ velocity_score * velocity_weight
+ discussion_score * discussion_weight
+ evidence_score * evidence_weight
+ authority_score * authority_weight
+ coverage_score * coverage_weight
```

第一阶段建议默认权重：

| 分项 | 默认权重 | 主要来源 | 说明 |
|---|---:|---|---|
| `attention_score` | 0.30 | 微博、知乎 | 反映公众关注强度 |
| `velocity_score` | 0.20 | 微博、知乎、新闻更新频率 | 反映短期升温 |
| `discussion_score` | 0.10 | 知乎 | 反映问题讨论和解释需求 |
| `evidence_score` | 0.20 | 新闻源、可引用来源 | 反映证据充分度 |
| `authority_score` | 0.10 | 人民网、新华网、央视网 | 反映权威报道强度 |
| `coverage_score` | 0.10 | 所有来源 | 反映跨平台覆盖 |

权重不是事实判断，只用于日报排序。不同模式可以调权：

```text
公共事件简报：提高 evidence_score / authority_score
娱乐热点简报：提高 attention_score / velocity_score
科技产品简报：提高 discussion_score / coverage_score
风险事件简报：提高 evidence_score，同时提高 guardrail 阈值
```

评分实现规则：

- 先做平台内归一化，再进入跨来源综合评分。
- 没有 `hot_value` 的新闻源不补造热度值，用报道数量、来源权重和时效性贡献分数。
- 没有官媒报道的社区事件仍可进入事件池和日报候选，但 `evidence_score` 较低。
- 只有官媒报道、社区无明显讨论的事件也可进入日报候选，但 `attention_score` 较低。
- `source_status = postpone` 的来源不参与真实评分，只能用于 mock 或后续候选说明。
- 综合评分必须保留 `score_detail_json`，说明每个来源贡献了哪些分项。

系统可以说：

```text
该事件获得多个新闻源报道
该事件在微博热搜中排名靠前
该事件在知乎热榜中形成讨论
该事件同时具备新闻报道和社区关注信号
该事件目前社区关注高，但新闻证据不足
该事件新闻证据较强，但社区关注度较低
```

系统不应轻易说：

```text
该事件全网最热
该事件真实影响最大
该事件已被证实
官媒已报道所以公众高度关注
社区热议所以事实已经成立
社区未讨论所以事件不重要
官媒未报道所以事件不存在
```

除非有足够来源、明确证据和 Guardrails 通过。

## 合规与公开边界

公开仓库中可以包含：

- 数据源分类。
- 选型标准。
- 字段映射说明。
- 标准化 schema。
- mock 数据。
- 脱敏示例。
- 采集器接口设计。

公开仓库中不应包含：

- 账号、Cookie、Token。
- 私有接口。
- 代理配置。
- 反爬绕过细节。
- 未脱敏原始样本。
- 平台限制细节和失败日志。
- 大规模评论抓取结果。

所有真实采集都应遵循低频、可解释、可关闭和可替换原则。采集失败时系统应降级，而不是依赖高风险方式强行补齐数据。

## 对模式 A 和模式 B 的影响

### 对模式 A

模式 A 依赖这些数据源完成热点简报：

```text
官媒 / 新闻源和 BBS / 社区源共同进入统一事件候选池
微博贡献即时关注、传播节奏和话题变化
知乎贡献讨论焦点、公众问题和解释型需求
新闻源贡献报道覆盖、证据强度和权威来源信号
```

第一阶段的重点是让这些来源稳定产出统一的 `NormalizedItem` 和 `EventSignal`。事件发现阶段不区分官媒优先或社区优先；热度排序阶段根据来源类型、字段质量和 source_status 计算不同评分贡献。

### 对模式 B

模式 B 后续会复用这些数据源产生的历史数据：

```text
items
events
platform_scores
event_snapshots
source_citations
```

v0.3 阶段只检索已有数据，不因为用户查询触发复杂实时采集。

如果指定事件在已有数据中没有证据，系统应返回：

```text
当前数据源未覆盖或证据不足
```

而不是编造来源或扩展到未验证平台。

## 第一阶段结论

v0.3 第一阶段建议采用以下数据源候选组合。真实 collector 只实现 `use` 或有明确降级方案的 `fallback` 来源；`postpone` 来源只用于 mock、产品说明或后续版本候选。

官媒 / 新闻源：

```text
人民网
中国新闻网
新华网
央视网
```

BBS / 社区源：

```text
微博热搜
知乎热榜
```

暂不接入：

```text
小红书
抖音
微信公众号全文
豆瓣小组深度采集
大规模评论抓取
B站热门 / 排行榜
```

这个组合可以覆盖事件确认、权威议程、即时关注和问题讨论，同时把第一阶段工程复杂度控制在可落地范围内。视频社区传播信号后续再评估，不作为 v0.3 第一阶段必需能力。
