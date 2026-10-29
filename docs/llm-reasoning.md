# DeepSeek 与原有规则的独立切换

[返回 README](../README.md) · [工作流](architecture.md) · [事件抽取](event-extraction.md)

## 当前状态与边界

2026-10-29 增加 `EVENT_REASONING_MODE=rules|llm`。默认 `rules`，填入 API Key **不会自动启用**。
初次实现通过模拟 HTTP 响应完成离线校验。用户配置 Key 后，2026-10-29 对10月23日保存材料进行有限的真实测试，结果与限制见末尾验证记录。未开始新采集、邮件投递或全量历史业务重算。

“二选一”针对语义判断后端，不替换来源协议、时间窗口、热度公式、日报模板和数据库事务：

| 环节 | rules | llm |
| --- | --- | --- |
| 事件信息抽取 | 原词典、正则、规则 | DeepSeek 独立抽取，不拼接规则字段 |
| 搜索增强相关性 | 原知乎规则／微博正文 reranker | DeepSeek 判断事实补充、相关意见、其他事情或信息不足 |
| 同平台／跨平台事件归并 | 原身份、召回、embedding、reranker 与约束 | 对当前窗口内候选分批进行 LLM 判断，不叠加旧模型分数 |
| 官媒补证匹配 | 原官媒匹配 | LLM 判断是否同一具体事件，出处和发布时间限制保留 |
| 失败 | 原降级行为 | 失败／待复核／未知，不自动切回 rules |

独立后台工具调用默认读取环境开关；每个正常分析任务在 `HotspotAnalysisInput.reasoning_mode` 中保存模式，并在工作流执行期间使用同一个上下文。已有任务的输入不可改写；切换后应创建新轮次。

## 配置方法

在**实际运行进程读取的私有环境文件**中配置，不能把 Key 发给前端或提交 Git：

```dotenv
# 保持旧方式：rules；用户准备好后才改为 llm。
EVENT_REASONING_MODE=rules
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
LLM_TIMEOUT_SECONDS=30
LLM_MAX_CALLS_PER_RUN=100
LLM_DOCUMENT_MAX_CHARS=1800
LLM_INPUT_MAX_CHARS=16000
LLM_MAX_OUTPUT_TOKENS=2200
LLM_CANDIDATES_PER_REQUEST=4
LLM_CACHE_HOURS=24
```

- 本地 Python：通常为 `backend/.env`，从 `backend` 目录启动。
- 正式 Docker Compose：默认是 `deploy/.env`，或 `APP_ENV_FILE` 指定的文件。
- 本地资源测试 Docker：当前模板使用 `deploy/.env.resource-test`，修改 `backend/.env` 不会自动传入容器。
- 更新 Docker 的环境文件后需要用原 Compose 参数**重新创建 backend 容器**；`docker restart` 只重启原容器，不重新读取 env_file。等用户准备好再操作，不中断已有采集。
- 请确认账户支持的实际模型名；模型可配置，历史回放已验证测试账户能调用 `deepseek-flash`，不保证其他账户权限或余额。接口参考：[DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)、[JSON Output](https://api-docs.deepseek.com/guides/json_mode/)。

正常启动分析前会检查选择的 LLM 配置是否齐全；缺 Key 时在付费来源采集之前报错。该检查只检查配置，不发“测试请求”。原 `EVENT_EXTRACTION_USE_LLM`、`use_llm` 及置信度开关保留兼容字段，但不再启用按条混合 refinement；应以新总开关为准。

独立 CLI 也支持 `--reasoning-mode rules` 或 `--reasoning-mode llm`，未显式指定时读取总开关。`MATCHING_PROFILE` 仍控制旧后端；LLM 后端不会因其设置而调用本地语义模型。

当前本地资源测试环境使用以下命令加载更新后的环境文件。在项目根目录执行，并先等待活动采集结束；命令只重新创建后端，数据库和 CLI 授权持久化卷保留：

```powershell
docker compose -p opinion-resource-test -f deploy/compose.resource-test.yml -f deploy/compose.resource-review.yml up -d --no-deps --force-recreate backend
docker exec opinion-resource-test-backend-1 python -c "from app.core.config import settings; print(settings.event_reasoning_mode)"
```

输出 `llm` 才表示新进程已加载模式。Compose 的显式 `environment` 配置优先于 `env_file`；历史任务的模式快照和已保存结果不会自动更新。此测试环境将 `backend/app` 挂载到容器，单纯修改该目录代码无需重建镜像，但需重启进程；镜像内的依赖或未挂载文件变化仍需重新构建。

## 结构化材料与证据

输入保留来源 ID、标题、正文、报道时间；微博包含最多三条已接纳 CLI 正文。LLM 不接收规则提取的实体列表或原模型分数作为裁决依据。每段文本有长度上限，截断会标记 `truncated`，整体输入超过预算则明确失败，不悄悄丢弃候选。

微博解析同时修复 `long_text.long_text_content` 未读取的问题：有有效长正文时优先采用，缺失或空白时回退 `text/text_raw/content`。该修复同时适用于两种模式，旧数据库与简报不自动重写。

抽取输出通过 Pydantic 严格校验，主体、动作、对象、地点、关键词和事件类型均带原文证据。发生时间只能是有证据、带时区的 ISO8601 值；不能把发布时间或采集时间自动填为发生时间。无证据字段留空，保留原始标题与来源身份。

事件比较必须覆盖每个候选 ID 恰好一次，只允许：

- `same_event`：同一具体事件；必须有双方可在输入原文中逐字找到的引用，且无冲突。
- `related_but_distinct`：同背景或相同人物的其他事情，保持独立。
- `different_event`：不同事件，保持独立。
- `insufficient_evidence`：信息不足，保留人工复核。

只有一个明确的 `same_event` 且其他候选均非信息不足时才自动合并；多个匹配目标或潜在竞争者转复核。模型不能自创候选 ID、把事件拆给多个目标、越过人工审核的明确结论或直接写数据库。界面的兼容数值 `1.0` 表示分类判定已通过结构与证据检查，不是模型给出的同事件概率；实际依据在 `llm_detail` 中。

搜索增强另外允许 `opinion_on_event`，使语境明确的批评、支持、期待等不会仅因没重复主体名称被排除。只有话题标签不够；同人物其他新闻不能当成本事件的意见。时间过期等来源约束先执行。待定内容保留审计，不自动形成每条评论的人工任务。

原文引用可验证只能证明“模型引用了提供的材料”，**不能证明材料为真或语义判定必然正确**。LLM 摘出的网友说法也不能自动变成官媒事实。

## 失败、费用与回放

- 缺少配置、网络异常、HTTP 错误、空响应、JSON／Schema 错误、输出截断、引用不存在均有明确错误码；不打印 Key 或供应商原始错误正文。
- 不自动重试 API 请求，避免超时后费用不明又重复调用。
- 每轮有最大调用数，沿用项目请求日志记录成功／失败、耗时；成功响应另外保存 token 用量。不预估真实价格或宣称已产生费用。
- 缓存按材料内容、完整提示词、提示词版本、Schema、模型与地址隔离；只保存通过校验的结果。默认 SQLite 缓存在业务数据库同目录的 `llm-cache.db`，可用 `LLM_CACHE_PATH` 指定，Docker 应放在持久化数据目录。
- Key 或模型响应失败时，抽取不产生伪造的规则补位结果；比较失败时不自动合并或判定独立，转待复核；官媒补证保留未知。
- 模式／模型／提示词版本进入采样签名，旧规则和 LLM 观测不直接计算热度趋势。

本次提供的测试使用 `httpx.MockTransport`，仅验证开关隔离、结构与引用校验、竞争候选、预算缓存和失败路径。真实模型材料回放记录见下方；不将小样本结果视为全量效果。用户配置好后，建议先对旧数据做独立的材料回放评估，再启动新采集；历史 FetchSourceItemsOutput 直接回放会沿用已保存的相关性结果，若要验证新的搜索过滤，需要从保留的原始响应重新规范化。不得把未实测结果写成准确率。

## 代码入口

- [`schemas/llm.py`](../backend/app/schemas/llm.py)：输出契约、提示词、版本。
- [`services/llm_client.py`](../backend/app/services/llm_client.py)：模式上下文、配置检查、HTTP、缓存和预算。
- [`services/llm_event_workflow.py`](../backend/app/services/llm_event_workflow.py)：材料整理、独立抽取、归并、相关性与官媒适配。
- [`test_llm_reasoning.py`](../backend/tests/test_llm_reasoning.py)：不联网的模拟响应测试。

本次没有改动或发布用户要求仅保留本地的自建评价数据集。

## 2026-10-29 真实历史材料验证

用户配置Key并授权后，以10月23日保存的材料进行了独立、有限的真实DeepSeek回放，不重新采集或覆盖原数据库。15条真实材料中14条抽取成功；10组配对中9组完成比较（4组合并、5组独立），1组因上游抽取时间格式失败而跳过。已完成配对与本次人工语义预期一致，但不代表全量准确率。另完成30条保存CLI记录的相关性检查、1组竞争候选和2项保存官媒材料匹配。

实测发现CLI纯标签误接纳、部分相关评论可能误排除，以及一次日期缺少时区导致的抽取失败。累计58次请求、72,831 tokens，包含第一次失败调用。未验证全量采集、官网实时搜索、新长正文恢复、日报或邮件。

第一次真实请求受DeepSeek默认思考模式影响，2,200个输出tokens全部用于推理，最终JSON为空。结构化请求现显式设置`thinking={"type":"disabled"}`，缓存身份同时纳入该参数；保留输出预算和不自动重试策略。修复后接口检查成功，相关离线测试30项通过。参考：[DeepSeek思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)。源码修复后应按部署方式重启后端进程；环境文件变化需要重新创建容器，镜像构建方式还需更新镜像。

另对本地保留的5份真实CLI原始响应做了离线解析检查：59条记录中39条有有效长正文，39条均正确选用 `long_text.long_text_content`。文件可能包含重复微博；该检查只验证解析选择，不等于验证新实时采集或长正文对归并质量的提升。

详细材料与结果保留在本地审计目录，未加入公开仓库，也未改写用户要求仅保留本地的自建评价数据集。
