# 事件匹配、归并与人工复核

[返回项目首页](../README.md) · [规则抽取](event-extraction.md) · [搜索增强](search-enrichment.md) · [官媒证据](official-evidence.md)

`match_and_resolve_events` 判断不同条目是否属于同一件事，复用稳定事件 ID。本文说明召回、比较、决策和模型运行方式；单平台事件不要求强行寻找跨平台对应项。

## 调用与数据流

```text
EventSignal + 来源引用
→ EventResolverAgent → EventMatcher
→ 规范身份 / URL / 知乎父问题身份
→ BM25 与 embedding 独立召回 → RRF 合并候选
→ 特征比较与 cross-encoder 重排
→ 实体、动作、时间等约束 + Guardrails
→ merge / create / candidate_review / reject / skip
```

[`MatchDocument`](../backend/app/services/match_documents.py)统一标题、摘要、实体、动作、关键词、时间提示、观测时间、URL 和规范身份。持久化事件优先读取 `event_detail_json.match_features`，兼容旧顶层字段；明确为空的字段不能被旧值偷偷补回。

身份命中可以跳过神经模型推理，但仍检查冲突。知乎问题与其回答共享 `zhihu:question:<id>`；微博 query 或 hashtag 只是候选线索，不等同于稳定身份。

## 召回与模型分工

| 方法 | 当前用途 | 限制 |
| --- | --- | --- |
| 身份／URL | 同条内容、同知乎问题的强关联 | 不能覆盖明确事实冲突 |
| BM25 | 基于候选集合 IDF、词频和长度归一化召回 | 中文使用字符 bigram，拉丁字母与数字按词处理 |
| N-gram | 可解释的字面重叠特征 | 泛词重合不等于同一事件 |
| Embedding | 独立语义召回和相似度特征 | 没有字面重叠也可召回，但不能单独自动合并 |
| RRF | 合并 BM25 与 dense 候选排名 | 合并排名，不把原始分数当同一种量纲 |
| Cross-encoder | 对候选重排并辅助判断 | 高分仍需要具体锚点和约束通过 |

当前在数据库候选范围内计算，未使用向量数据库或近似向量索引。相关服务见 [`matching_engine.py`](../backend/app/services/matching_engine.py) 与 [`event_matcher.py`](../backend/app/agents/event_matcher.py)。

## 决策与阈值

事件匹配同时使用规则、模型与约束，不是只按 rerank 的三个档位分类。当前配置来自 [`EventMatchConfig`](../backend/app/schemas/matching.py)：

| 配置 | 默认值 | 作用 |
| --- | ---: | --- |
| `auto_merge_confidence_threshold` | 0.85 | 综合匹配置信度达到自动合并档位，仍须通过护栏 |
| `candidate_review_confidence_threshold` | 0.60 | 综合置信度进入人工复核档位 |
| `rerank_auto_merge_min_score` | 0.85 | rerank 辅助自动合并的门槛，不能独立覆盖冲突 |
| `rerank_reject_below` | 0.10 | 实际 rerank 分低于此值且无强身份／URL／标题包含依据时，否认候选关联 |
| `embedding_candidate_min_similarity` | 0.78 | embedding 候选相关配置 |
| `embedding_auto_merge_min_similarity` | 0.86 | embedding 强相似约束判断 |
| `time_window_same_event_hours` | 72 | 同事件匹配默认时间跨度，区别于日报 24 小时窗口 |

具体行为：

- 明确身份或 URL 命中可复用事件，但明确冲突优先拒绝。
- 综合高分且约束、Guardrails 通过才自动合并；弱话题或仅高语义相似可转人工。
- 中间档进入 `candidate_review`，待确认条目不提前加入候选事件的来源证据。
- 综合分低于复核阈值，否认与该候选的自动归并。普通发现信号可建立独立事件；搜索增强在自动模式下仍受父话题约束，不能自行建热点。
- 实际 rerank 分低于 0.10 时，在无强身份／URL／标题包含依据的情况下直接分开，不因弱信号再把这个独立热榜话题送回合并复核。分数缺失不视为零；明确冲突仍先处理。0.10 是保守初始阈值，模型分数不是“同一事件概率”，后续需用标注样本校准。
- 可用的知乎／微博热榜发现信号即使缺少实体和动作，也可用原始标题独立创建话题并参与分类。当前要求去掉两端 `#` 后标题至少 4 个字符，来源状态包含 `use`，且不是搜索增强信号；保留抽取质量标记，不把标题当成已核实的事实。跨事件合并仍执行原有护栏。

以上规则对后续分析生效，不自动改写已有人工审核决定、待审任务或历史日报。离线测试覆盖低分边界、强身份复用、弱标题合并复核，以及三个 RSS 标题连续两轮独立入库和单平台分类。

共享约束覆盖已识别的日期、年份、型号、部分地点／桥梁、动作和灾害类型冲突。系统保留初始特征与最多三份代表描述，防止连续相似条目串错事件；这不是通用中文事实推理或完整聚类质量保证。

## 持久化与审计

`event_signal_id` 标识本轮信号，`event_fingerprint` 辅助匹配，`Event.event_id` 才是跨轮次稳定业务身份。没有将 `first_seen_at` 冒充事件发生时间；抽取中的 `event_time_hint` 也可能只是发布时间提示。

每次比较保存 `MatchFeatures`，包括身份、标题包含、关键词／N-gram、BM25、embedding、rerank、实体／动作、时间距离、缺失字段和冲突。模型未执行的分数保留 `null`，不伪造分数。

匹配结果经 [`event_resolver.py`](../backend/app/agents/event_resolver.py)决定落库。高分没有通过约束、父话题未确定或需要人工处理的关系不贡献跨平台覆盖。人工队列保存具体 Item、候选事件和判断依据。

后台可处理归并、单独创建、忽略或拒绝，搜索增强仍受创建限制。确认后按原观测时间重算，生成更正草稿；已保存日报内容不变。见 [Guardrails 与人工复核](guardrails.md)。

## 模型安装与运行

| `MATCHING_PROFILE` | 执行方式 |
| --- | --- |
| `rules` | 身份、规则、BM25 等，不加载神经模型 |
| `hybrid` | 增加 embedding 与混合召回 |
| `hybrid_rerank` | 增加 cross-encoder；项目默认配置 |

当前使用 `BAAI/bge-small-zh-v1.5`（512 维）与 `Xenova/bge-reranker-base` 的量化 ONNX 权重。后者来源于 BGE reranker，当前评估对应 int8 转换，不应写成原始浮点模型结果。

在 `backend` 目录、已创建虚拟环境后执行：

```powershell
..\.venv\Scripts\python.exe -m pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu
..\.venv\Scripts\python.exe -m pip install -e ".[dev,semantic]"
..\.venv\Scripts\python.exe scripts/prepare_semantic_models.py
```

准备脚本固定 revision 并生成 `manifest.json`，模型默认写到忽略目录 `backend/data/models`。准备阶段需要下载，运行阶段只读本地权重。当前组合见 [`pyproject.toml`](../backend/pyproject.toml)；Docker 使用[Linux 锁定依赖](../backend/requirements-linux.lock)和 CPU 镜像。

| 配置 | 默认／用途 |
| --- | --- |
| `SEMANTIC_MODEL_DIR` | 未设置时使用项目 `backend/data/models`；容器为 `/models` |
| `SEMANTIC_DEVICE` | `cpu`，embedding 设备 |
| `SEMANTIC_CPU_THREADS` | `4` |
| `SEMANTIC_CACHE_PATH` | 可配置 reranker SQLite 分数缓存；Docker 为 `/app/data/semantic-cache.db` |

有进程内缓存及可选的跨进程精确文本对分数缓存。缓存只复用同模型、同文本对的模型输出，事件时间、身份、阈值和归并决策每次重新检查，不缓存业务决定。

分析运行中模型不可用时保留降级标记并回退规则；生产启动在非 `rules` 模式先检查必须的本地模型文件，缺失时拒绝启动。离线模型对照评估要求实际加载并推理成功，不允许静默退化成三次规则测试。

## 官媒共用能力与历史修复

官媒匹配复用比较字段和检索能力，但独立判断“这条报道能否作为该事件依据”，不共用一套自动接受阈值。字段读取、空动作误匹配与来源／时间要求见[官媒证据](official-evidence.md#官媒误匹配的字段与判定规则改动)。

历史评估显示仍有漏匹配与需人工判断的情况。保守地不自动合并并不总是代码故障；评估必须区分低召回、正确转人工和真正错配，见[评估方法](evaluation.md)。

## 高分语义归并与跨轮复核

2026-10-18 增加缺少实体时的高分语义加标题文字支持路径，保留明确冲突和多候选歧义保护；同一待审关系跨轮去重。阈值、适用范围和限制见[采集窗口与归并说明](collection-windows-and-merging.md#自动归并规则)。
