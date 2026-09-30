# 匹配与官媒补证实现更新（2026-09-30）

本次在现有九步 Agent/Tool 链路内完成接口修复、候选检索、真实模型推理和官媒定向搜索。事件合并与官媒补证共用比较服务，保留各自的业务判定规则。结果见 [评估报告 v0.2](../reports/evaluation-v0.2.md)；[v0.1](../reports/evaluation-v0.1.md) 保留原始评估数字，并标注了后续进展。

## 当前调用关系

```text
match_and_resolve_events Tool
  → EventResolverAgent → EventMatcher
  → ID / URL / 知乎父问题身份
  → BM25 与 embedding 分别召回 → RRF 合并候选
  → 共享文本比较 + cross-encoder → 事件约束 / Guardrails
  → merge / create / candidate_review / reject

classify_events Tool
  → 本地官媒证据池
  → 未获支持的社区事件生成 query → 官媒站点搜索（有预算、缓存）
  → 搜索条目落库 → 共享文本比较 → 官媒支持判定 → 分类
```

这些阶段是现有 Tool 内调用的 service，没有把所有内部函数伪装成新注册的 Tool。Agent 仍是可审计的固定 Plan；日报 Tool 保留，未新增 LLM 动态规划。

## 官媒误匹配的字段与判定规则改动

以 v0.1 的“养老金事件被安哥拉讲座误判为官媒支持”为例，问题分为**事件字段没有读对**和**缺失字段被错误地算作匹配**两层。现在的处理顺序是：取出事件真实特征 → 比较候选报道 → 检查具体事件依据 → 判断来源与时间是否足以给出 `supported`。

| 环节 | v0.1 当时的问题 | 当前实现 |
|---|---|---|
| 事件特征读取 | 持久化把实体、动作写在 `event_detail_json.match_features`，官媒匹配只找顶层，读到空值。 | [统一适配器](../backend/app/services/match_documents.py)优先读取嵌套的 `entities`、`action_terms`、`event_time_hint`，再兼容旧记录的顶层字段；嵌套字段明确为空时，不用旧字段补成“有值”。[官媒匹配器](../backend/app/services/official_support.py)通过该适配器读取事件特征。 |
| 缺失值计算 | 事件动作为空时，旧逻辑会从候选报道取动作，再与该候选自身比较，产生虚假的 `action_overlap=1`。 | 动作与实体都以**目标事件**的字段为比较起点。目标字段为空，重叠值就是 0，并记录缺失；不会用候选自己的词代替目标事件的词。 |
| 候选相关性 | 动作、时间或部分字面重叠可能令无关报道获得支持。 | 先排除已识别的日期、型号、地点、动作等明确冲突；双方都有实体却没有交集也拒绝。随后既要有足够文本相关性，又要有标题包含、事件实体、共同具体对象（如型号／桥梁）或至少两个关键词的完整覆盖之一。只有动作相同、发布时间接近或部分 N-gram／关键词重叠都不够。 |
| 语义模型 | v0.1 未执行 embedding 与 rerank。 | 候选池超过上限时可用 BM25 与 embedding 召回；比较阶段可用 rerank 辅助排序。rerank 高分只有同时具备实体和动作锚点时才可补强文本匹配，不能越过明确冲突或具体依据条件。 |
| 支持级别 | 误匹配会带动分类升级，例如 E → D。 | 通过相关性检查后，来源状态、URL、标题、发布时间和时间窗口都满足要求才给 `supported`；已判相关但来源、字段或时间条件不足则为 `weak_supported`。不相关返回 `not_found`，不会把“没有可靠依据”包装成弱支持。 |

具体字段链路是 `Event.event_detail_json.match_features` → `event_document()`／`_event_data()` → `_match_candidate()`；候选报道则由 `Item.normalized_json`／标题和摘要进入 `item_document()`。比较特征、模型分数与缺失标记保留在候选审计中。`weak_supported` 仍是官媒支持的预测正例，因此评估没有靠降级状态掩盖误匹配。

[v0.2 同集对照](../reports/evaluation-v0.2.md#同集对照与真实模型执行)显示，原有 13 条官媒用例的 FP 从 5 降到 0；扩充后的 46 条里，`rules`、`hybrid`、`hybrid_rerank` 三种配置的官媒结果相同，均为 TP=10、FP=0、FN=4、TN=32。这说明本轮可观察到的官媒误匹配改善主要来自字段与规则修复，不能算作 rerank 单独带来的收益。仍有 4 条官媒正例漏检；用例标签待人工复核，现有人工审核队列也不会自动接收官媒相关性疑难项。

## 数据契约与规则修复

- `MatchDocument` 统一标题、摘要、实体、动作、关键词、事件时间提示、发布时间、观测时间、URL 和规范身份。读取 `event_detail_json.match_features`，同时兼容旧顶层字段；显式空值不从旧字段补回。
- 缺失实体、动作记为缺失，重叠值为 0；不再拿候选自己的动作与自身比较。
- 知乎问题与其回答共享 `zhihu:question:<id>`。确定身份无需加载模型；明确冲突仍不能被身份高分覆盖。
- 匹配不再用 `first_seen_at` 冒充事件时间。当前抽取器的 `event_time_hint` 仍可能由发布时间推导，是提示而非已经验证的事发时刻。
- 修正实体截断，避免把“大学宣布”等整个动作短句当成学校名，也避免把“上市／市场”简单当作城市。规则抽取仍可能漏实体或抽错实体。
- 保留事件初始特征和最多三份代表描述，新增非身份成员需要检查代表描述中的冲突，降低连续相似消息串错事件的风险。这不是完整的聚类算法或聚类质量评估。
- 知乎已知短榜、空榜的 `list_complete` 正确传递；未知搜索质量不再提高分类置信度。

## 检索与判定的边界

BM25 使用候选集合的 IDF、词频饱和和长度归一化。中文以字符 bigram 为 token，拉丁字母和数字保留为词。embedding 与 BM25 独立召回，采用 RRF 合并名次；某个候选没有字面重叠，也可以经 dense 路径进入比较。当前在数据库时间窗口内直接计算向量，没有向量数据库或近似索引。

N-gram 是字面重叠特征，embedding 用于语义召回和比较，cross-encoder 用于重排及判定辅助。它们的分数都不是“同一事件概率”。事件匹配保留历史加权规则；官媒规则使用有方向的覆盖度，两者不共用一个自动接受阈值。

共享约束检查标题中明确的日期、型号、款年、可识别地点／桥梁名称、部分动作和灾害类型冲突。它是有限的确定性规则，不是通用中文实体识别或事实蕴含模型。规则未覆盖的对象、主体角色、否定和事件进展仍可能有歧义。

事件自动合并需要身份或足够的具体约束；只有高语义分的候选进入复核。官媒支持还要求标题强包含、具体实体／对象锚点或完整关键词覆盖；单纯动作相同、搜索命中、部分关键词或 N-gram 相近不构成证据。`weak_supported` 表示已判相关但来源／时间等不完整，不表示“相关性可疑也可以使用”。

模型不可用时保留 `null` 分数和明确质量标记，退回规则结果，任务标记 `partial`；不伪造向量或语义分数。规则模式是显式选择，不算模型故障。

## 模型安装与运行

本机验证环境为 Windows、Python 3.11、CPU。使用：

- embedding：`BAAI/bge-small-zh-v1.5`，512 维。
- reranker：`Xenova/bge-reranker-base` 的 `onnx/model_quantized.onnx`，源模型为 `BAAI/bge-reranker-base`；本报告评估的是该 int8 转换，不等同于原始浮点权重结果。
- Torch 2.8.0 CPU、Sentence Transformers 5.7.0、Transformers 4.57.6、SentencePiece 0.2.0、ONNX Runtime 1.20.1。版本与模型修订也写入评估 JSON。

在 `backend` 目录执行：

```powershell
& ../.venv/Scripts/python.exe -m pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu
& ../.venv/Scripts/python.exe -m pip install -e ".[dev,semantic]"
& ../.venv/Scripts/python.exe scripts/prepare_semantic_models.py
```

下载脚本固定模型 revision，并保存 `manifest.json`。权重位于被 Git 忽略的 `backend/data/models`。运行阶段只加载本地文件；不自动下载，也不调用付费模型接口。embedding 可配置设备，当前 ONNX reranker 固定使用 CPU。模型采用批处理和进程内有界缓存；未接入持久化向量缓存。

本机曾遇到较新 Torch 的 DLL 初始化错误，以及较新 SentencePiece／ONNX Runtime 的原生崩溃，因此使用已验证的组合，reranker tokenizer 使用 `use_fast=False`。

| 设置 | 默认值 | 含义 |
|---|---|---|
| `MATCHING_PROFILE` | `hybrid_rerank` | `rules` / `hybrid` / `hybrid_rerank` |
| `SEMANTIC_MODEL_DIR` | `backend/data/models` | 可指定本地模型目录 |
| `SEMANTIC_DEVICE` | `cpu` | embedding 设备 |
| `SEMANTIC_CPU_THREADS` | `4` | CPU 推理线程 |
| `OFFICIAL_SEARCH_ENABLED` | `true` | 对未获支持的社区事件补查官媒 |
| `OFFICIAL_SEARCH_MAX_EVENTS` | `3` | 每轮至多补查三个事件，每事件两个源 |
| `OFFICIAL_SEARCH_LIMIT` | `5` | 每源保留至多五条 |
| `OFFICIAL_SEARCH_TIMEOUT_SECONDS` | `10` | HTTP 客户端超时 |
| `OFFICIAL_SEARCH_CACHE_MINUTES` | `30` | 同事件同 query 缓存窗口；同 run_id 复用 |

CLI 可指定 `--matching-profile rules` 或 `--no-official-search`。`--replay` 默认关闭官媒联网；显式 `--official-search` 才会补查。模型推理不需要网络，但真实采集本身仍受数据源环境影响。

## 官媒搜索实测边界

实际接入中新网 GET 和人民网 POST 搜索；仅解析返回数据，不执行网页脚本。限定官方域名，候选条目只贡献证据和权威性，不贡献社区热度。保存 query、来源状态、HTTP 状态、候选 Item ID 与引用；全部失败为 `not_checked`，有效空结果为该次搜索范围内的 `not_found`，部分失败保留质量标记。

[2026-09-29 实测](../reports/official-search-validation-2026-09-29.md)：中新网八次请求可用；人民网四次请求返回 HTTP 405。已实现失败降级，不能声称两个官媒源均已可用。搜索结果必须再判断相关性，旧报道或同名主体命中不直接升级事件类别。

参考：[Sentence Transformers 检索与重排](https://www.sbert.net/examples/sentence_transformer/applications/retrieve_rerank/README.html)、[BGE embedding 模型说明](https://huggingface.co/BAAI/bge-small-zh-v1.5)、[BGE reranker 说明](https://bge-model.com/bge/bge_reranker.html)、[RRF 说明](https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion)。本项目采用上述方法的本地实现，不依赖 Elasticsearch 服务。
