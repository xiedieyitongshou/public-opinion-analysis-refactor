"""A hotlist topic may exist without asserting that its title is a verified fact."""


def can_create_hotlist_topic(signal: dict) -> bool:
    roles = set(signal.get("source_roles") or [])
    return bool(
        len(str(signal.get("title") or "").strip().strip("#")) >= 1
        and str(signal.get("title") or "").strip().strip("#") not in {"热议", "热搜", "热点"}
        and set(signal.get("platforms") or []) & {"weibo", "zhihu"}
        and roles & {"topic_discovery_signal", "attention_signal"}
        and "search_enrichment_signal" not in roles
        and "use" in (signal.get("source_statuses") or [])
    )
