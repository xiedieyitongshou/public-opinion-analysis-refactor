# Day 1–Day 47 项目衔接与前端开发就绪度检查

检查日期：2026-09-26。依据为当前工作区的 `实施计划-v0.3.md`、`docs/`、`reports/`、后端源码和现有测试。本文记录检查结论，不代表相关缺口已修复。

**当前可以开始前端布局、组件、模拟数据展示和部分 Ops 页面；尚不适合把后端视为已经完成，直接开展完整业务数据联调。** 设计、数据结构和领域计算已有较多可复用成果，主要阻断集中在阶段间的数据传递、事件持久化、观测口径和展示接口。

计划本身也没有把 Day 47 定为全部后端完成点：第七周为 Day 43–49，Day 48–49 还有 Evaluation Runner 和真实事件评测；Day 50–53 还包括日报结构、Briefing Agent、Critic Agent 和日报发布保护，Day 54 才是 Web 首页。定时任务、服务器部署位于 Day 58–59，不应把这些未来任务缺失算作 Day 47 的实现缺陷，也不必等到部署完成才开始前端。

验证范围：本次会话运行后端全量测试，结果为 **187 passed、2 条依赖弃用提示**；`ruff check app tests` 通过。随后用内存 SQLite、合成条目和已有测试 fixture 做跨模块诊断。诊断使用真实业务函数，只有采集响应被替换为本地 fixture；没有调用知乎、微博、官媒或 LLM 的真实外部服务，没有修改业务代码或正式数据库。

现有测试主要证明模块规则及局部持久化行为；跨模块诊断发现的问题说明，全量测试通过不能代替端到端验收。

下面按计划逐日核对。“基本具备”仅针对该日范围；设计日不要求当日完成所有运行功能，“模块具备”也不代表已接入完整业务链。

| 周 / 日 | 对照交付项 | 当前完成情况与衔接判断 |
|---|---|---|
| 第 1 周 Day 1 | 模式 A / B、产品边界 | 有 `docs/product-modes.md`，模式 A 优先、模式 B 预留明确；仍有“综合热度分”等早期描述，与后续分平台口径冲突。 |
| Day 2 | 来源分类、选型与降级 | 有 `docs/data-sources.md`，后续采样和 collector 反映了知乎 API、微博 RSSHub + CLI、官媒证据池的选择。 |
| Day 3 | 模式 A 工作流与阶段职责 | 有 `docs/workflows.md`；自动分析与人工发布分工明确，但其中综合热度、部分来源范围和阶段名称未完全跟随后续修改。 |
| Day 4 | 工具清单、输入输出、重试与副作用 | 有 `docs/tools.md` 和工具注册代码；需要将最新 Day 43–47 的 service 编排顺序补回主流程，不能仅凭工具名已注册认定链路已通。 |
| Day 5 | 生命周期、保留与存储策略 | 有 `docs/data-lifecycle.md`，符合本日设计目标；清理和归档等运行机制仍不能按“文档存在”算已实现。此项不阻止组件开发。 |
| Day 6 | 工作流与 Structured Output 规范 | 有文档和多组 Pydantic schema；草稿阻断状态、旧评分字段与新展示结构需要统一。 |
| Day 7 | Guardrails / Evaluation 设计、周复盘 | 两类设计文档存在，后续也有规则及样例；未找到计划指定的 `docs/project-log/week-01.md`。周报缺失本身不阻断前端。 |
| 第 2 周 Day 8 | FastAPI、依赖、数据库连接骨架 | 基本具备，可导入应用、生成 OpenAPI、通过健康检查测试；启动流程未调用 `init_db`，README 仍主要是 Day 8 骨架说明。 |
| Day 9 | 任务、工具日志、复核、违规、评测模型 | 模型及相关测试存在。`EvaluationRun/Case` 表存在不等于 Evaluation Runner 已存在。 |
| Day 10 | 来源、条目、事件、评分、快照、日报模型 | 模型存在，已增加稳定业务 `event_id` 和观测去重约束；事件自动写库及条目关联仍是后续衔接缺口。 |
| Day 11 | Tool Registry、校验、调用日志 | 基本具备；目前 schema 合法即记工具调用成功，业务输出的 `failed/skipped` 不自动成为任务失败或跳过。主编排需要明确处理。 |
| Day 12 | Plan / PlanStep / TaskGraph | 线性计划和依赖顺序具备；没有上一步输出到下一步输入的绑定。默认日报计划无法继续承接真实采集结果。 |
| Day 13 | 状态机与结构化校验 | 单项具备并有测试；草稿状态机与展示 schema 枚举不一致，任务状态与业务结果也需衔接。 |
| Day 14 | 完整 mock plan 与运行时复盘 | 现有 runner 测试验证的是单步搜索占位计划；本次对当前默认多步计划的诊断在事件抽取处失败。未找到 `week-02.md`。 |
| 第 3 周 Day 15 | 官媒 RSS 字段采样 | 有来源探测与结构报告，提供字段缺失和 fallback 依据；属于历史样本证据，不代表今日网络可用性。 |
| Day 16 | 知乎授权 API、字段与额度验证 | 有客户端、smoke 脚本及访问报告；历史采样验证已记录，实际持续使用仍依赖运行环境与额度。 |
| Day 17 | 知乎热榜到搜索增强采样 | 有采样脚本与报告；相应搜索 collector 后来已实现，但日常编排没有自动从本轮热榜生成搜索候选。 |
| Day 18 | 官媒结构和曝光字段边界 | 有专项报告；“官媒覆盖不等于公众热度”的边界被后续评分设计承接。 |
| Day 19 | 微博 RSSHub、CLI、跨平台样本 | 有相关采样报告和脚本；成功样本证明路径可行，不能替代连续运行验收。 |
| Day 20 | A–F 分类及排序验证 | 有跨源分类报告；报告真实样本覆盖 C/E/F，A/B/D 未有本轮真实正例，后续单元 fixture 不应被表述为真实命中率。 |
| Day 21 | 第四周设计输入确认 | 来源角色、字段边界、搜索相关性规则已被第四周 schema 承接；早期报告中的 RSS 顺序 rank 需按最新口径解释为 list_position。 |
| 第 4 周 Day 22 | NormalizedItem 和字段映射 | schema、字段映射文档及测试基本具备；字段存在不保证 collector、Normalizer、数据库使用同一身份和排名位置。 |
| Day 23 | SourceSignal / EventSignal / 搜索增强边界 | schema 和相关性过滤具备；社区条目到 SourceSignal 的统一运行适配和数据库 ID 回溯尚未接入主流程。 |
| Day 24 | SourceCitation / EventCard / DailyBriefing | schema 基本具备，可作为前端契约起点；当前 HTTP 响应没有直接提供这些完整展示对象。 |
| Day 25 | A–F 分类与字典序排序 | 规则函数和单元用例具备；需与最新分平台趋势排序及展示口径共同冻结契约。 |
| Day 26 | Guardrails Runtime 与基础 API | 默认规则、违规写库和 Ops API 具备；事件列表返回数据库摘要及自由 JSON，尚非首页聚合响应。 |
| Day 27 | 采样驱动评测样例 | `day27_evaluation_cases_v0_1.json` 和测试存在；它是样例集，不是运行当前整链的质量报告。 |
| Day 28 | schema 复盘与 collector 清单 | 对应能力和文档存在；`week-04.md` 实际主要记录 Day 29–35，阶段命名需要整理。 |
| 第 5 周 Day 29 | Collector 基类、注册、验证模式 | 具备 fetch/parse/normalize/save、来源闸门和验证模式；quota 字段有预留，报告明确 live quota 接入仍待完成。 |
| Day 30 | 知乎 hot_list collector | HTTP client、collector 和故障处理具备；真实 collector 的 rank 在 `raw_metrics/normalized` 内，Day 47 却读顶层 rank，存在实测衔接缺陷。 |
| Day 31 | zhihu_search 增强 | 查询、过滤、审计基本具备；没有显式 query/candidate 会跳过，默认主流程尚未把热榜候选传入。 |
| Day 32 | 微博 RSSHub + CLI | 组合客户端与降级具备；跳过置顶后的覆盖完整性、有效采样参数还需与 Day 47 对齐。 |
| Day 33 | 官媒 RSS evidence collectors | 人民网、中国新闻网、新华网 fallback 的 collector 具备；RSS 证据池不会自动完成社区事件补证与分类。 |
| Day 34 | 日常采集与验证调度 | 两种计划及日志具备，但日常计划只有采集一步；传入的顶层 `run_id` 没有转发给采集输入，无法直接满足 Day 47 固定轮次要求。 |
| Day 35 | 来源质量与验证复盘 | 有 `crawl-validation-v0.1.md`；报告明确是测试支持的基线，真实连续采集、额度统计与回放闭环未获该报告证明。 |
| 第 6 周 Day 36 | 确定性 Normalizer | 清洗、时间、去重键、匹配文本等函数具备；collector 已先保存旧 hash，Normalizer 会重算不同 hash，需要统一 ID 或明确映射及回写。 |
| Day 37 | 事件抽取与来源可追溯 schema | 严格 EventSignal、引用检查等结构具备；真实流水线尚需保证每个 item/source signal 的 ID 能回到数据库条目。 |
| Day 38 | 规则抽取与 LLM fallback 预留 | 规则抽取可用；DeepSeek 默认 adapter 仍抛出未实现异常，按本日“预留”范围可接受，当前不能声称真实 LLM refinement 已完成。 |
| Day 39 | 事件匹配、稳定 ID、语义辅助 | 规则、ID/URL、词项相似和决策具备；候选由调用方传入，无自动 DB 召回。所谓 embedding 分支目前为词频余弦，未接入真正语义向量。 |
| Day 40 | 合并 Guardrails / Event Resolver | Guardrails 和违规持久化具备；Resolver 返回决策，未自动创建/合并 Event、关联 Item 或调用复核入队工具。 |
| Day 41 | 复核队列与人工确认 API | 入队、查询、决策审计存在；人工 merge 只追加事件审计，不关联 Item；create_new_event 会建 Event，但同样未关联条目。 |
| Day 42 | 真实采集数据的归并评测 | 未找到针对当前 Day 36–41 完整链路的独立真实归并评测报告；现有公开样例主要来自 Day 27 及更早采样。 |
| 第 7 周 Day 43 | 官媒支撑识别 | 纯匹配 service、结果 schema、引用及写入对象 helper 具备；需编排者查询官媒池、传入并提交结果。 |
| Day 44 | 分平台评分 | 单次评分与数据库写入具备；现有注册评分工具不写 observation_id/observed_at，Day 46 严格读取会排除这些分数。 |
| Day 45 | 社区补证 / 官媒议程双路径 | 两条 service 路径、adapter、覆盖统计与能力报告存在；没有官媒定向搜索运行客户端，路径 B 无 DB 候选召回/事件落库，主链未调用双路径。 |
| Day 46 | presence 派生与分类组装 | 函数、固定窗口规则、JSON 覆盖保存和测试具备；没有 Classification Agent/主编排把官媒结果、事件决策、来源信号、评分统一喂入。 |
| Day 47 | 24h 观测、时长与分平台趋势 | 纯计算、快照去重保存、分析组装、展示一致性检查及测试具备；真实采集输出的排名/完整性、run_id、采样间隔配置和主流程接入仍未闭合。 |

从用户操作到前端响应的关键路径，当前衔接情况如下。

| 交接位置 | 已有成果 | 仍需补齐 |
|---|---|---|
| 运行入口 → 每轮采集 | CollectorAgent、采集计划、工具日志 | 固定 run_id/window_end；可复现的一轮分析命令或服务入口 |
| 采集 → 统一标准化 → SourceSignal | 来源映射、Normalizer、schema | 输入输出绑定、统一 item ID/hash、社区 adapter、标准化结果回写 |
| EventSignal → Event | Matcher、Guardrails、决策结果 | DB 候选召回、create/merge 幂等写库、来源和条目关联、复核任务入队 |
| Event → Day 43–47 | 官媒匹配、平台评分、分类、趋势 service | 同轮数据组装；先保存来源观测及评分，再分类和趋势分析；正确处理失败/partial |
| 分析结果 → 前端 | EventCard/DailyBriefing schema、部分 Ops API | 展示组装器、明确响应 schema 的业务读取 API、空数据/来源失败/未知趋势状态 |
| 日报 → 正式发布与长图 | 表结构、基础 guardrails、占位工具 | 按 Day 50–55 实现模板、Briefing、Critic、发布保护及渲染 |

以下是实际复现结果，可用于后续修复验收。均为离线诊断，不消耗外部 API 额度。

| 诊断 | 实际结果 | 影响 |
|---|---|---|
| 成功采集 1 条 fixture，执行当前默认日报 plan | fetch 成功；normalize 收到空输入，输出 skipped/0 条；extract 因缺 run_id、source_signals 失败 | 第 2 周运行时未与第 5–6 周工具契约接通 |
| 给日常采集 plan 传入 run_id | 生成的 fetch 输入没有 run_id | 不能直接交给要求稳定轮次的 Day 47 |
| 对强事件信号调用 Resolver，传内存数据库 | 输出 create、created_event_count=1；实际 events 行数=0 | 返回“创建”是决策计数，下游没有可评分事件 |
| 预先关联 Event/Item，调用 Day 44 评分，再调用 Day 46 DB 组装 | 分数中 zhihu_topn=true；observation_id/observed_at 均缺失；最终 presence=false、category=unknown | 即使人工补了事件，两个正式入口仍不兼容 |
| 同一知乎条目依次经过客户端映射和 Normalizer | 两个 content_hash 不同 | 必须补贯穿采集结果、数据库和解析结果的身份映射，不能假定 hash 相同 |
| 人工确认 merge_to_existing，引用明确 item_id | 返回 succeeded，复核状态 resolved，item.event_id 仍为 null | 用户“合并成功”后评分/趋势仍可能读取不到条目 |
| 无 DB 调用评分工具 | ToolResult.status=succeeded，业务 output.status=failed | 主任务和前端运行状态需要消费业务状态，不能只看工具调用状态 |
| 使用真实知乎 collector 和 fixture client 生成 rank 10→5 两轮输出，再记录 Day 47 | raw_metrics.rank 为 10/5，顶层 rank=null；观测 rank=null；趋势 basis=platform_score，rank_delta=null | 热榜排名依据在交接处丢失，前端无法显示正确的排名变化依据 |
| 使用真实微博 collector、3 条 RSS fixture、skip_top=1 | 请求成功，返回 2 条，scope=top3:skip=1，list_complete=false | 有意跳过置顶与意外截断尚未正确区分，持续时长可能一直按不完整降级 |
| 检查实际 OpenAPI | 11 个路径，均为 /health 或 /ops；没有首页、榜单、日报业务读取路径 | schema 文件中的 EventCard/DailyBriefing 尚不是前端可取到的响应 |

关键代码定位：

- 计划与数据传递：[planning.py](../backend/app/agents/planning.py)、[runner.py](../backend/app/agents/runner.py)。
- 采集保存与身份：[base.py](../backend/app/collectors/base.py)、[normalizer.py](../backend/app/agents/normalizer.py)、[zhihu_client.py](../backend/app/services/zhihu_client.py)。
- 事件写库和复核：[event_resolver.py](../backend/app/agents/event_resolver.py)、[human_review.py](../backend/app/services/human_review.py)。
- 评分和分类入口：[default_tools.py](../backend/app/tools/default_tools.py)、[classification_assembly.py](../backend/app/services/classification_assembly.py)。
- 真实观测适配：[platform_trend_assembly.py](../backend/app/services/platform_trend_assembly.py)、[collectors/zhihu.py](../backend/app/collectors/zhihu.py)、[collectors/weibo.py](../backend/app/collectors/weibo.py)。
- 官媒双路径：[official_paths.py](../backend/app/services/official_paths.py)。
- 前端响应与发布状态：[display.py](../backend/app/schemas/display.py)、[state_machine.py](../backend/app/agents/state_machine.py)、[api/ops.py](../backend/app/api/ops.py)。

前端契约在正式冻结前还需处理这些文档和代码差异：

| 差异 | 当前证据 | 建议采用的交付口径 |
|---|---|---|
| 时间窗口边界 | 展示报告写 `[start,end)`，Day 46/47 文档和代码用 `(start,end]` | 以最新 Day 46/47 的 UTC `(start,end]` 为计算契约；上海时区仅作展示与 report_date 标签 |
| 观测时间字段 | 展示报告使用 snapshot_time，模型实际为 snapshot_at，来源时间为 observed_at | 明确来源 observed_at 与快照 snapshot_at，避免以 calculated_at 代替真实采集时间 |
| 草稿阻断状态 | 展示 schema 为 draft_blocked，状态机为 draft_blocked_for_publish | 统一一个枚举后再生成前端类型 |
| 首页顶层结构 | 展示报告提出 DailyHotspotDisplay/PlatformEventCard；代码目前是 DailyBriefing/EventCard；两者都没有业务读取 API | 在 Day 50 明确最终响应结构和栏目，再做前端真实数据适配 |
| 微博排名含义 | 早期采样报告用 RSS 顺序 rank；新趋势契约禁止把它当官方 rank | 展示榜单序号与来源 rank 分开；微博仅说明样本强度和连续出现时间 |
| 官媒 Top5 query 策略 | 展示报告要求 event_id+report_date 每日一次；代码没有该选择和执行记录 | 如保留此策略，增加实际执行与状态，未运行保持 not_checked；不要在前端推断 not_found |
| 综合热度和第一阶段来源范围 | 第一周部分文档仍提综合热度及 B站等，后续计划收敛为知乎/微博分平台分析 | 依据最新阶段契约展示，更新旧文档的适用版本 |

相关文档：[展示数据结构报告](mvp-display-mode-data-structure-v0.1.md)、[最新趋势说明](../docs/platform-trend.md)、[分类组装说明](../docs/hotspot-classification-assembly.md)、[采集验证基线](crawl-validation-v0.1.md)、[跨源分类采样报告](cross-source-classification-probe-v0.1.md)。历史报告不应因为后续已写单元测试，就被解释为当前完整链路的真实验证报告。

建议把接下来的工作分为可以同步开始的前端工作，以及真实联调前必须完成的后端收尾：

| 前端范围 | 是否可现在开始 | 条件 |
|---|---|---|
| 页面布局、导航、平台切换、事件卡、来源徽标、unknown/partial 提示 | 可以 | 用确认后的 schema 与固定 fixture；不把 mock 页面视作真实链路验收 |
| Ops 来源/内容/任务/日志/违规列表 | 可以先接 | 初始化测试库，按已有 /ops API 开发；展示任务状态时区分工具调用与业务结果 |
| 人工复核列表、详情和操作表单 | 可做界面 | 真正 merge/create 的条目关联副作用修复后再验收操作 |
| 真实知乎/微博榜单、A–F 分类、持续时间与趋势 | 暂不能直接联调完成 | 修复上游衔接并提供同 run、同窗口的业务读取响应 |
| 今日简报、发布流程、长图导出 | 可做样式原型 | 还需完成计划 Day 50–55 对应后端与渲染工作 |

推荐的最短收尾顺序：

1. 补一个可从空测试库运行到分析结果的统一入口。入口可以先是 CLI/内部 service；前端不需要触发分析时，不必为此先实现任务提交 API。传递固定 run_id/window_end，绑定步骤输出，处理业务 failed/partial/skipped。
2. 统一 Item、SourceSignal、EventSignal 和稳定 Event.event_id 的映射；实现数据库候选召回、自动 create/merge、Item 关联、候选复核入队，以及人工决策的真实关联副作用。
3. 修正 collector 到观测层的 rank、skip_top/覆盖完整性和有效采样参数；配置采样间隔。正式评分保存来源 observation_id/observed_at；统一旧评分入口与观测评分入口，避免下游静默排除。
4. 将官媒池补证、受限搜索增强、Day 46 分类与 Day 47 趋势串入同一 run。先写真实观测及分数，再分类和分析；按产品选择实现官媒定向 query 或明确的 RSS-only fallback。
5. 结合 Day 48–50 补评测并冻结展示契约。至少覆盖正常、单源故障、搜索低相关、复核待处理、无数据和不同窗口等状态；统一上表中的窗口、排名、枚举和顶层结构。
6. 实现展示组装器及前端必需读取接口，然后进行真实业务联调。按需要提供“最新分平台热点/分类”“事件详情/来源”和“最新简报”读取能力；不要求现在引入复杂队列或部署设施。

进入真实首页联调的最小验收条件应可直接观察：空库能初始化；一轮采集可生成已关联条目的稳定事件；至少两轮来源观测可在同窗口形成分平台分析；同一轮重跑不重复建事件或快照；来源失败能返回可解释的 partial/unknown；业务读取 API 返回完整展示对象；待复核项不会自动被当成已确认合并；前端无需自行重算分类、热度或趋势。端到端 fixture 通过后，再以小额度真实采集做一次完整验证，并将持续采集质量纳入 Day 48–49 评测。

目前的完成程度更准确地描述为：**设计与领域模块已形成较完整基础，采集和局部运维能力可用，端到端业务衔接及前端数据出口仍待收尾。前端可以并行启动，后端也需要继续完成上述收尾和原计划第八周的服务工作。**
