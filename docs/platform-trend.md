# Day 47：滚动 24 小时分平台热度分析

Day 47 以稳定 `Event.event_id` 和真实来源采集轮次为输入，分别输出知乎、微博的连续观测上榜时间、当前 Day 44 平台分和趋势。结果保存在 `events.event_detail_json.platform_heat_analysis`，原始轮次保存在 `event_snapshots.metrics_json.platform_observations`。`PlatformScore.score_detail_json` 记录关联的 `observation_id`、`observed_at`、评分配置和采样口径；仅有 `calculated_at` 的旧评分不能构造新观测。

使用顺序：

1. `fetch_source_items` 传入稳定的 `run_id`。采集结果包含 `observation_id`、实际 `observed_at`、`topn_scope`、完整性和采样口径。`validate_only` / `dry_run` 不进入正式时间序列。
2. 事件融合完成后，调用 `analyze_event_heat`，传入采集输出、已跟踪的 `event_ids`，以及新关联内容的 `event_ids_by_content_hash`。已有 `Item.event_id` 关联会自动复用。工具为每个已跟踪事件保存主榜单 true / false / unknown 观测；知乎搜索和微博 CLI 单独出现时不生成上榜时长。
3. Day 46 分类与 Day 47 分析使用同一个 `run_id` 和固定 `window_end`。分析默认沿用 `classification_detail.window_end`；也可显式传入。两者均读取 UTC `(window_end - 24h, window_end]`。同一分析 run 重试必须使用原窗口。
4. 为 `sources.fetch_interval_minutes` 配置主来源调度间隔，或在 `analyze_event_heat.configs` 传入各平台的 `expected_interval_minutes`。未配置时仍保留可用静态热度，但连续时长和趋势为 unknown。

调用示例（已取得 `FetchSourceItemsOutput` 且完成事件融合）：

```python
from app.schemas import AnalyzeEventHeatInput, PlatformTrendConfig
from app.tools.default_tools import analyze_event_heat_handler
from app.tools.runtime import ToolContext

result = analyze_event_heat_handler(
    AnalyzeEventHeatInput(
        run_id="daily-2026-09-26",
        event_ids=["stable-event-id"],
        collection=collection_output,
        event_ids_by_content_hash={"item-content-hash": "stable-event-id"},
        configs={
            "zhihu": PlatformTrendConfig(expected_interval_minutes=60),
            "weibo": PlatformTrendConfig(expected_interval_minutes=60),
        },
    ),
    ToolContext(db_session=db),
)
```

`analyze_platform_trend` 是纯计算函数。当前连续时长只连接相邻 true 观测；false、unknown、范围变化或超长间隔切断。知乎优先比较真实热榜排名；微博 RSSHub 顺序不作为排名，只有同口径且带 CLI 补强的分数才可能给出升降温。展示读取结果前应使用 `current_event_heat_analysis` 校验 Day 46 / Day 47 的 run 和窗口一致；`EventCard` 也会执行此检查，并仅把明确指定的 `primary_platform` 趋势映射到兼容字段。
