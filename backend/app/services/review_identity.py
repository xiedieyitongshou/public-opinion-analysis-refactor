"""Stable review identity, separate from per-run event signal IDs."""

import hashlib
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def topic_identity(payload):
    sources = payload.get("identity_sources") or payload.get("source_citations") or []
    keys = set()
    for source in sources:
        url = urlsplit(str(source.get("url") or ""))
        host = (url.hostname or "").lower()
        if host == "zhihu.com" or host.endswith(".zhihu.com"):
            match = re.search(r"/question/(\d+)", url.path)
            if match:
                keys.add("zhihu:question:" + match[1])
                continue
        if host in {"weibo.com", "weibo.cn"} or host.endswith((".weibo.com", ".weibo.cn")):
            title = source.get("title") or payload.get("title") or ""
            title = re.sub(r"\s+", " ", title.strip().strip("#").strip())
            if title:
                keys.add("weibo:topic:" + title)
            continue
        if source.get("source_type") == "official_news" or host.endswith(
            ("people.com.cn", "people.cn", "chinanews.com.cn", "chinanews.com", "news.cn",
             "xinhuanet.com")
        ):
            # Preserve content-bearing query parameters; strip only known tracking keys.
            query = [(k, v) for k, v in parse_qsl(url.query, keep_blank_values=True)
                     if not k.lower().startswith("utm_")]
            keys.add("official:" + urlunsplit(
                (url.scheme.lower(), url.netloc.lower(), url.path, urlencode(sorted(query)), "")
            ))
    # Unknown identity is intentionally run-scoped; never infer identity from a generic title.
    return sorted(keys) or ["signal:" + str(payload.get("event_signal_id") or "")]


def conflict_signature(payload):
    features = payload.get("match_features_json") or {}
    flags = sorted(set(features.get("guardrail_flags", []) + payload.get("guardrail_flags", [])))
    conflicts = [f for f in flags if "conflict" in f]
    signal = payload.get("event_signal") or {}
    facts = {k: signal.get(k) for k in ("entities", "action_terms", "event_time_hint")}
    # Keep explicit conflict facts: two different date conflicts must not share a decision.
    return {"flags": flags, "conflict_facts": facts if conflicts else None}


def relation_key(review_type, payload):
    value = [review_type, topic_identity(payload), payload.get("candidate_event_id"),
             conflict_signature(payload)]
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def merge_evidence(old, new):
    merged = {**old, **new}
    for key, single in (("run_ids", "run_id"), ("event_signal_ids", "event_signal_id"),
                        ("item_ids", None), ("source_signal_ids", None)):
        values = [*old.get(key, []), *new.get(key, [])]
        if single:
            values += [old.get(single), new.get(single)]
        merged[key] = list(dict.fromkeys(v for v in values if v is not None))
    for key in ("source_citations", "identity_sources"):
        values = [*old.get(key, []), *new.get(key, [])]
        merged[key] = list({json.dumps(v, sort_keys=True, default=str): v for v in values}.values())
    history = []
    for p in (old, new):
        record = {k: p.get(k) for k in ("run_id", "event_signal_id", "confidence",
                                       "match_features_json")}
        for entry in p.get("evidence_history") or [record]:
            if entry not in history:
                history.append(entry)
    merged["evidence_history"] = history
    return merged
