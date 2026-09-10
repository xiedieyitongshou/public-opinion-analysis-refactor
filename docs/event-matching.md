# 事件匹配与语义重排设计

## 文档目标

本文定义微博话题、知乎问题 / 搜索结果和官媒新闻如何在事件层面进行匹配、重排和人工复核。

核心原则：

- 事件匹配是确定性工具和可审计特征驱动，不让 LLM 凭直觉合并事件。
- embedding 用于语义召回和 rerank，不单独决定自动合并。
- BM25 / n-gram 保留可解释召回能力，适合短文本和关键词强相关场景。
- 实体、动作、对象和时间窗口是自动合并的硬约束。
- 中置信度结果进入异步人工复核队列，不阻断自动采集分析链路。

## 当前基线

当前已完成的采样脚本使用规则型相关性过滤：

```text
same_question_id
candidate_title_contained / topic_title_contained
query_contained
keyword_overlap_high
```

其中 `keyword_overlap_high` 使用 2-6 gram 和词片段集合计算重合度，当前采样阈值为 `0.55`。

这套方法优点是可解释、低成本、容易回放；缺点是对同义改写、跨平台表达差异和新闻标题 / 社区话题长短不一致的情况召回不足。

## 推荐方案

第一阶段采用混合匹配：

```text
ID / URL 硬匹配
-> 标题强包含和实体规则匹配
-> BM25 / n-gram 候选召回
-> embedding 语义召回或 rerank
-> 混合 rerank
-> Guardrails 决策
-> auto_merge / candidate_review / reject
```

## Agent 执行链路

```text
Collector Agent
-> fetch_source_items
-> normalize_raw_items

Normalizer Agent
-> build_event_text_for_match
-> build_event_text_for_embedding

Event Resolver Agent
-> extract_event_signals
-> hard_match_by_id_url
-> bm25_ngram_retrieve_candidates
-> embed_event_text
-> semantic_retrieve_candidates
-> rerank_event_matches
-> match_event
-> apply_merge_guardrails
-> auto_merge / enqueue_human_review / reject

Classification Agent
-> classify_event_priority
-> calculate_category_rank
-> explain_evidence_chain
```

Agent 的职责是编排工具、选择降级路径、汇总结构化结果和生成解释。相似度计算、阈值判断、合并写库和复核入队应由确定性工具完成。

## 工具职责

| 工具 | 作用 | 失败降级 |
|---|---|---|
| `hard_match_by_id_url` | 使用平台 ID、URL、知乎 question id、微博 mid 做强匹配 | 无命中时进入下一步 |
| `bm25_ngram_retrieve_candidates` | 用 `event_text_for_match` 做可解释候选召回 | 无命中时仍可尝试 embedding |
| `embed_event_text` | 为 `event_text_for_embedding` 生成向量 | embedding 服务不可用时跳过语义召回 |
| `semantic_retrieve_candidates` | 基于 cosine similarity 召回语义相近候选 | 只作为候选，不直接合并 |
| `rerank_event_matches` | 综合规则、BM25 / n-gram、embedding、实体和时间进行排序 | 缺少 embedding 时使用规则 + BM25 |
| `match_event` | 输出 `should_merge`、`confidence`、`reason` 和 `match_features_json` | 中置信度进入复核 |
| `apply_merge_guardrails` | 阻止实体冲突、时间冲突和单纯 embedding 误合并 | 写入审计和复核队列 |

## 匹配特征

每次匹配必须保存 `match_features_json`：

```json
{
  "id_match": false,
  "url_match": false,
  "title_containment": false,
  "keyword_overlap": 0.0,
  "ngram_overlap": 0.0,
  "bm25_score": 0.0,
  "embedding_similarity": null,
  "entity_overlap": 0.0,
  "action_overlap": 0.0,
  "object_overlap": 0.0,
  "time_distance_hours": null,
  "source_pair": ["weibo", "zhihu"],
  "matched_by": ["bm25_ngram", "embedding_rerank"],
  "hard_constraints_passed": false,
  "guardrail_flags": []
}
```

## 决策规则

### 自动合并

满足以下任一条件可以自动合并：

- 同平台稳定 ID 或 URL 命中。
- 跨平台标题强包含，且实体 / 动作 / 对象一致，时间窗口合理。
- BM25 / n-gram 高分、embedding 高相似、实体和时间硬约束均通过。

### 人工复核

以下情况进入 `candidate_review`：

- embedding 高相似，但实体、动作或对象缺失。
- BM25 / n-gram 高分，但 embedding 一般或标题存在歧义。
- 搜索结果相关，但存在历史内容污染风险。
- 官媒报道与社区话题可能相关，但时间窗口或实体不完整。

### 拒绝合并

以下情况不合并，只写审计：

- 核心实体冲突。
- 时间窗口明显冲突。
- 只有泛词相似，例如“苹果 华为”“开学考试”“新能源车”。
- 只有 embedding 高相似，但没有实体、动作、对象或时间支撑。
- 搜索结果低相关或明显历史污染。

## embedding 使用边界

embedding 适合：

- 同一事件的不同表述。
- 官媒新闻标题和社区话题之间的语义差异。
- 长标题和短话题之间的候选召回。
- 用户指定事件搜索的语义扩召回。

embedding 不适合单独处理：

- 极短泛词话题。
- 同名不同事件。
- 实体相同但时间不同的连续报道。
- 历史内容污染。
- 营销词和平台热词。

因此，embedding 只能提升召回和排序，不能绕过实体、时间和来源证据 guardrails。

## 置信度建议

| 置信度 | 条件 | 动作 |
|---|---|---|
| high | 硬匹配，或规则强匹配 + BM25 / n-gram + embedding 均支持，且硬约束通过 | 自动合并 |
| medium | 部分信号支持但存在实体、时间或语义不完整 | 进入人工复核 |
| low | 只有弱关键词或弱语义相似 | 不合并，写审计 |
| rejected | 实体冲突、时间冲突、低相关搜索结果 | 拒绝合并 |

## Evaluation 要求

评测集必须单独统计：

- 规则强匹配 precision / recall。
- BM25 / n-gram 召回带来的新增正确合并和误合并。
- embedding rerank 带来的新增正确合并和误合并。
- `candidate_review` 的人工负担。
- 泛词、短话题、历史内容污染和同名不同事件的误合并率。

只有当 embedding 在真实样本上提升召回且误合并可控时，才允许提高它在自动合并中的权重。
