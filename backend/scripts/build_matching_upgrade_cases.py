"""Freeze additional manually labeled cases; requires local live-search captures only to rebuild."""

import ast
import hashlib
import json
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "backend/app/evaluation/cases/system_cases_v0_2.json"
OUT = BASE.with_name("system_cases_v0_3.json")
NOW = "2026-09-29T12:30:00+00:00"

SCENARIOS = [
    (
        "海星汽车宣布召回2026款A7电动车，涉及制动软件缺陷",
        "A7刹车程序存在隐患，海星汽车宣布召回2026款车辆",
        "海星汽车发布2027款A7电动车",
    ),
    (
        "南城市青河区明日起停水12小时，因供水管道抢修",
        "青河区供水管线故障将维修，南城市公告次日断水半天",
        "北城市青河区明日起停水12小时，因供水管道抢修",
    ),
    (
        "星河大学宣布2026年取消早八课程",
        "2026年起，星河大学本科课不再安排在上午八点",
        "星河大学宣布2026年新开设夜间课程",
    ),
    (
        "青江市发布暴雨红色预警，中小学临时停课",
        "受强降雨影响，青江全市学校暂停线下教学，气象预警升为红色",
        "青江市发布高温红色预警，提醒市民防暑",
    ),
    (
        "蓝星科技9月28日发布折叠屏X2手机",
        "X2折叠机正式亮相，蓝星科技于9月28日举办发布会",
        "蓝星科技9月28日发布平板T2",
    ),
    (
        "临海市跨江大桥9月28日正式通车",
        "临海跨江大桥于9月28日开放交通",
        "临海市东湾大桥9月28日正式通车",
    ),
    (
        "明远制药获批上市新型流感疫苗",
        "药监部门准予明远制药新流感疫苗进入市场",
        "明远制药获批上市新型新冠疫苗",
    ),
    (
        "西岭山区发生6.2级地震，当地启动应急响应",
        "6.2级地震袭击西岭，救援响应随即启动",
        "东岭山区发生6.2级地震，当地启动应急响应",
    ),
]


def synthetic(title, key, *, official=False):
    return {
        "source_id": "chinanews_search" if official else "zhihu_hot_list",
        "source_name": "synthetic official" if official else "synthetic community",
        "source_type": "official_news" if official else "community_question_hotlist",
        "source_status": "use",
        "source_origin": "official_search" if official else "mock",
        "platform": "chinanews" if official else "zhihu",
        "signal_role": "evidence_signal" if official else "attention_signal",
        "title": title,
        "url": f"https://fixture.invalid/upgrade/{key}",
        "content_hash": hashlib.sha256(key.encode()).hexdigest(),
        "fetched_at": NOW,
        "published_at": NOW,
        "raw_metrics": {},
    }


def main():
    suite = json.loads(BASE.read_text(encoding="utf-8"))
    suite["version"] = "0.3"
    suite["description"] += "；新增语义改写、近似不同事件、多候选召回及真实官媒 query 候选。"
    suite["provenance"]["upgrade"] = {
        "live_date": "2026-09-29",
        "labels": "assistant annotations pending human review",
        "public_capture_fields": "titles/URLs/times only; no full text or credentials",
        "natural_positive_limit": (
            "two additional same-publisher interview reports; not cross-platform"
        ),
        "synthetic_scenarios": "fictional entities; independent labels, not production accuracy",
    }
    splits = {group: case["split"] for case in suite["cases"] for group in case["groups"]}

    def add(key, kind, origin, split, groups, data, expected, rationale):
        suite["cases"].append(
            {
                "case_id": key,
                "kind": kind,
                "origin": origin,
                "split": split,
                "groups": groups,
                "input": data,
                "expected": expected,
                "rationale": rationale,
            }
        )

    for index, (target, positive, negative) in enumerate(SCENARIOS):
        group = f"upgrade-fiction-{index}"
        split = "dev" if index < 6 else "holdout"
        for positive_label, text in ((True, positive), (False, negative)):
            suffix = "positive" if positive_label else "negative"
            left = {"item": synthetic(target, f"{index}-target")}
            right = {"item": synthetic(text, f"{index}-{suffix}")}
            official = {"item": synthetic(text, f"{index}-official-{suffix}", official=True)}
            rationale = (
                "同一具体事件的不同措辞。"
                if positive_label
                else "对象、地点、产品或动作不同；话题相近不足以认定同一事件。"
            )
            add(
                f"upgrade-match-{index}-{suffix}",
                "matching",
                "synthetic",
                split,
                [group],
                {"left": left, "right": right},
                {"same_event": positive_label},
                rationale,
            )
            add(
                f"upgrade-official-{index}-{suffix}",
                "official_support",
                "synthetic",
                split,
                [group],
                {"event": left, "official_items": [official]},
                {"supported": positive_label},
                rationale,
            )
        candidates = [
            {"id": "near-topic", "document": {"title": negative}},
            {"id": "correct", "document": {"title": positive}},
            {"id": "unrelated", "document": {"title": "市民周末参加公园音乐节"}},
            {"id": "generic", "document": {"title": "今日新闻及行业背景综述"}},
        ]
        add(
            f"upgrade-retrieval-{index}",
            "retrieval",
            "synthetic",
            split,
            [group],
            {
                "target": {"title": target},
                "candidates": candidates,
                "limit": 2,
                "relevant_ids": ["correct"],
            },
            {"all_relevant_recalled": True},
            "相关候选应进入 Top-2；标签不参与召回计算。",
        )

    raw = ROOT / "notes/source-probe-raw"
    captures = [
        json.loads((raw / name).read_text(encoding="utf-8"))
        for name in (
            "official-search-upgrade-20260929.json",
            "official-search-expanded-20260929.json",
        )
    ]
    # These labels refer to the specific source events, not merely query-token occurrence.
    batches = [
        (captures[0]["results"][5], "live-02", [False] * 5),
        (captures[1]["results"][2], "live-01", [False] * 5),
        (captures[1]["results"][3], "live-00", [False] * 5),
    ]
    interview = deepcopy(captures[1]["results"][0])
    interview["items"] = [interview["items"][2], interview["items"][4]]
    batches.append((interview, "live-16", [True, True]))
    for batch_index, (batch, target_ref, labels) in enumerate(batches):
        target_group = suite["corpus"][target_ref]["group"]
        split = splits[target_group]
        for index, (record, same) in enumerate(zip(batch["items"], labels, strict=True)):
            entry = {
                key: val
                for key, val in record.items()
                if key
                in {
                    "source_id",
                    "source_name",
                    "source_type",
                    "source_status",
                    "source_origin",
                    "platform",
                    "signal_role",
                    "title",
                    "url",
                    "published_at",
                    "fetched_at",
                    "external_id",
                    "content_hash",
                    "quality_flags",
                    "signal_contribution_role",
                }
            }
            title = entry["title"]
            if title.startswith("['") and title.endswith("']"):
                title = " ".join(ast.literal_eval(title))
            entry["title"] = title
            key = f"query-{batch_index}-{index}"
            group = key
            suite["corpus"][key] = {
                "group": group,
                "item": entry,
                "query": batch["query"],
                "source": "live official site search",
            }
            groups = [target_group, group]
            reason = (
                "不同 URL 的报道描述同一运动员赛后采访；同一媒体，不算自然跨平台泛化。"
                if same
                else "真实 query 命中，但报道对象、比赛阶段或年份不同。"
            )
            add(
                f"upgrade-live-match-{batch_index}-{index}",
                "matching",
                "real_excerpt",
                split,
                groups,
                {"left": {"ref": target_ref}, "right": {"ref": key}},
                {"same_event": same},
                reason,
            )
            add(
                f"upgrade-live-official-{batch_index}-{index}",
                "official_support",
                "real_excerpt",
                split,
                groups,
                {"event": {"ref": target_ref}, "official_items": [{"ref": key}]},
                {"supported": same},
                reason,
            )

    OUT.write_text(json.dumps(suite, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(suite['cases'])} cases to {OUT}")


if __name__ == "__main__":
    main()
