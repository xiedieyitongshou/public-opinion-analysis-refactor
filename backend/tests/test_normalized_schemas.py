import pytest
from pydantic import ValidationError

from app.schemas import NormalizedItem


def base_item() -> dict[str, object]:
    return {
        "source_id": "zhihu_hot_list",
        "source_name": "知乎热榜",
        "source_type": "community_hotlist",
        "source_status": "use",
        "source_origin": "official_api",
        "platform": "zhihu",
        "signal_role": "attention_signal",
        "title": "事件 A",
        "url": "https://www.zhihu.com/question/1",
        "fetched_at": "2026-09-10T10:00:00+08:00",
        "raw_metrics": {"rank": 1},
        "quality_flags": [],
    }


def test_normalized_item_accepts_day22_core_fields() -> None:
    item = NormalizedItem.model_validate(base_item())

    assert item.source_status == "use"
    assert item.source_origin == "official_api"
    assert item.signal_role == "attention_signal"
    assert item.raw_metrics["rank"] == 1


def test_normalized_item_requires_quality_flag_for_missing_url() -> None:
    payload = base_item()
    payload["url"] = None

    with pytest.raises(ValidationError, match="missing_url"):
        NormalizedItem.model_validate(payload)

    payload["quality_flags"] = ["missing_url"]
    assert NormalizedItem.model_validate(payload).url is None
