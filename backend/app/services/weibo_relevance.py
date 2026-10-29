"""Body-based CLI relevance; uncertain samples never require human review."""

import math
import re

from app.core.config import settings
from app.services.event_constraints import title_conflicts, title_facts
from app.services.semantic_models import SemanticUnavailable, get_semantic_models


def clean_body(text: str, parent: str) -> str:
    # Preserve other hashtags as text, but do not score a copied query as evidence.
    target = re.sub(r"\s+", "", parent).strip("#").casefold()
    text = re.sub(
        r"#([^#\n]+)#",
        lambda m: " " if re.sub(r"\s+", "", m[1]).casefold() == target else m[1],
        text,
    )
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"(?:来源|转自|编辑|记者)\s*[:：].*$", "", text)
    text = re.sub(r"[（(][^()（）]{1,30}(?:消防|日报|新闻|融媒|发布|记者)[）)]\s*$", "", text)
    return re.sub(r"\s+", " ", text.replace("\u200b", "")).strip()


def explicit_conflicts(parent: str, body: str) -> list[str]:
    # Title rules are not NER. Apply only to a short declarative lead, never a roundup.
    lead = re.split(r"[。！？!?\n]", body, maxsplit=1)[0]
    if len(lead) > 120 or re.search(
        r"相比|对比|以前|此前|去年|不是|并非|是否|希望|例如|比如|而是", lead
    ):
        return []
    conflicts = title_conflicts(parent, lead)
    # Headings such as 【驾车安全注意事项】 can pollute the title regex's place span.
    # If the parent's place is explicitly present, do not infer a different location.
    if any(place in lead for place in title_facts(parent)["location"]):
        conflicts = [flag for flag in conflicts if flag != "location_conflict"]
    # Dates can denote publication/history; differing actions can be an update.
    allowed = {
        "location_conflict", "model_conflict", "model_year_conflict", "named_object_conflict"
    }
    if ("取消" in parent and "增设" in lead) or ("增设" in parent and "取消" in lead):
        allowed.add("action_conflict")
    if re.search(r"发生于|发生在", parent) and re.search(r"发生于|发生在", lead):
        allowed.add("explicit_date_conflict")
    return [flag for flag in conflicts if flag in allowed]


def classify_body(parent: str, text: str) -> tuple[str, float, list[str], list[str]]:
    from app.services.llm_client import reasoning_mode

    if reasoning_mode() == "llm":
        from app.services.llm_event_workflow import evaluate_relevance

        relation = evaluate_relevance(parent, text, source="weibo_cli", user_query_id="body-check")
        return relation.decision, relation.confidence, relation.matched_by, relation.quality_flags
    body = clean_body(text, parent)
    conflicts = explicit_conflicts(parent, body)
    if conflicts:
        return "rejected", 0.0, [], conflicts
    if len(re.sub(r"\W", "", body)) < 8 or not parent.strip():
        return "audit_only", 0.0, [], ["insufficient_body_evidence"]
    if settings.matching_profile != "hybrid_rerank":
        return "audit_only", 0.0, [], ["weibo_relevance_reranker_disabled"]
    try:
        score = float(get_semantic_models().rerank([(parent, body)])[0])
        if not math.isfinite(score) or not 0 <= score <= 1:
            raise SemanticUnavailable("invalid_reranker_score")
    except SemanticUnavailable:
        return "audit_only", 0.0, [], ["weibo_relevance_reranker_unavailable"]
    if score < settings.weibo_relevance_reject_below:
        return "rejected", score, ["body_reranker"], ["body_relevance_low"]
    if score >= settings.weibo_relevance_accept_at:
        return "accepted", score, ["body_reranker"], []
    return "audit_only", score, ["body_reranker"], ["body_relevance_uncertain"]
