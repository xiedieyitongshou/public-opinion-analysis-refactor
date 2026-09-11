"""Default MVP guardrail rules for Day 26."""

from __future__ import annotations

from typing import Any

from app.guardrails.registry import GuardrailRegistry, GuardrailRule
from app.schemas import GuardrailCheckInput, GuardrailCheckResult

PUBLIC_HEAT_CLAIMS = ("全网热议", "公众热议", "广泛关注", "全民关注")
FACT_CLAIMS = ("证实", "确认", "事实证明", "已坐实")


def build_default_guardrail_registry() -> GuardrailRegistry:
    registry = GuardrailRegistry()
    for rule in (
        GuardrailRule(
            name="briefing.no_source_citation",
            rule_type="briefing_publish",
            description="Block event cards without source citations.",
            handler=check_event_cards_have_citations,
        ),
        GuardrailRule(
            name="source.missing_url",
            rule_type="source_quality",
            description="Block event cards where every citation lacks a source URL.",
            handler=check_event_cards_have_source_urls,
        ),
        GuardrailRule(
            name="classification.community_without_official_requires_conservative",
            rule_type="classification_quality",
            description="Warn when community-only events are not marked for conservative language.",
            handler=check_community_without_official_is_conservative,
        ),
        GuardrailRule(
            name="classification.official_only_not_public_heat",
            rule_type="classification_quality",
            description="Block official-only cards that claim public heat.",
            handler=check_official_only_not_public_heat,
        ),
        GuardrailRule(
            name="source.audit_only_search_not_supporting",
            rule_type="source_quality",
            description="Block audit-only search enrichment from contributing to classification.",
            handler=check_audit_only_search_not_supporting,
        ),
        GuardrailRule(
            name="summary.single_community_source_fact_claim",
            rule_type="summary",
            description="Block community-only cards that make fact-certainty claims.",
            handler=check_single_community_source_fact_claim,
        ),
    ):
        registry.register(rule)
    return registry


def check_event_cards_have_citations(input_data: GuardrailCheckInput) -> GuardrailCheckResult:
    cards = _event_cards(input_data.payload)
    missing = [card.get("event_id") for card in cards if not card.get("source_citations")]
    if missing:
        return _result(
            "briefing.no_source_citation",
            "briefing_publish",
            "block",
            "Event cards without source citations cannot enter publishable briefing.",
            input_data,
            {"event_ids": missing},
            blocks_publish=True,
        )
    return _pass("briefing.no_source_citation", "briefing_publish", input_data)


def check_event_cards_have_source_urls(input_data: GuardrailCheckInput) -> GuardrailCheckResult:
    bad: list[str | None] = []
    for card in _event_cards(input_data.payload):
        citations = card.get("source_citations") or []
        if citations and not any(citation.get("url") for citation in citations):
            bad.append(card.get("event_id"))
    if bad:
        return _result(
            "source.missing_url",
            "source_quality",
            "block",
            "At least one source URL is required for publishable event cards.",
            input_data,
            {"event_ids": bad},
            blocks_publish=True,
        )
    return _pass("source.missing_url", "source_quality", input_data)


def check_community_without_official_is_conservative(
    input_data: GuardrailCheckInput,
) -> GuardrailCheckResult:
    risky: list[str | None] = []
    for card in _event_cards(input_data.payload):
        if card.get("official_support_status") != "not_found":
            continue
        evidence = card.get("evidence_summary") or {}
        if not evidence.get("conservative_language_required"):
            risky.append(card.get("event_id"))
    if risky:
        return _result(
            "classification.community_without_official_requires_conservative",
            "classification_quality",
            "warn",
            "Community events without official support must use conservative language.",
            input_data,
            {"event_ids": risky},
            review_required=True,
        )
    return _pass(
        "classification.community_without_official_requires_conservative",
        "classification_quality",
        input_data,
    )


def check_official_only_not_public_heat(input_data: GuardrailCheckInput) -> GuardrailCheckResult:
    bad: list[str | None] = []
    for card in _event_cards(input_data.payload):
        if card.get("priority_category") != "F_official_only":
            continue
        text = _card_text(card)
        if any(claim in text for claim in PUBLIC_HEAT_CLAIMS):
            bad.append(card.get("event_id"))
    if bad:
        return _result(
            "classification.official_only_not_public_heat",
            "classification_quality",
            "block",
            "Official-only events cannot be described as public heat.",
            input_data,
            {"event_ids": bad, "blocked_terms": list(PUBLIC_HEAT_CLAIMS)},
            blocks_publish=True,
        )
    return _pass(
        "classification.official_only_not_public_heat",
        "classification_quality",
        input_data,
    )


def check_audit_only_search_not_supporting(input_data: GuardrailCheckInput) -> GuardrailCheckResult:
    bad: list[str | None] = []
    for card in _event_cards(input_data.payload):
        detail = card.get("classification_detail") or {}
        if detail.get("audit_only_signal_count", 0) and detail.get("used_audit_only_as_support"):
            bad.append(card.get("event_id"))
    if bad:
        return _result(
            "source.audit_only_search_not_supporting",
            "source_quality",
            "block",
            "Audit-only search enrichment must not support classification.",
            input_data,
            {"event_ids": bad},
            blocks_publish=True,
        )
    return _pass("source.audit_only_search_not_supporting", "source_quality", input_data)


def check_single_community_source_fact_claim(
    input_data: GuardrailCheckInput,
) -> GuardrailCheckResult:
    bad: list[str | None] = []
    for card in _event_cards(input_data.payload):
        presence = card.get("platform_presence") or {}
        has_official = bool(presence.get("official_source"))
        community_count = sum(
            1 for key in ("zhihu_topn", "weibo_topn") if presence.get(key)
        )
        if has_official or community_count != 1:
            continue
        text = _card_text(card)
        if any(claim in text for claim in FACT_CLAIMS):
            bad.append(card.get("event_id"))
    if bad:
        return _result(
            "summary.single_community_source_fact_claim",
            "summary",
            "block",
            "Single-community-source cards cannot make fact-certainty claims.",
            input_data,
            {"event_ids": bad, "blocked_terms": list(FACT_CLAIMS)},
            blocks_publish=True,
        )
    return _pass("summary.single_community_source_fact_claim", "summary", input_data)


def _event_cards(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payload.get("event_cards"), list):
        return [card for card in payload["event_cards"] if isinstance(card, dict)]
    sections = payload.get("sections")
    if not isinstance(sections, list):
        return []
    cards: list[dict[str, Any]] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        section_cards = section.get("event_cards")
        if isinstance(section_cards, list):
            cards.extend(card for card in section_cards if isinstance(card, dict))
    return cards


def _card_text(card: dict[str, Any]) -> str:
    evidence = card.get("evidence_summary") or {}
    parts = [
        card.get("title"),
        card.get("summary"),
        evidence.get("lead"),
        " ".join(evidence.get("platform_evidence") or []),
        " ".join(evidence.get("official_evidence") or []),
        " ".join(evidence.get("discussion_evidence") or []),
    ]
    return " ".join(part for part in parts if isinstance(part, str))


def _pass(
    rule_name: str,
    rule_type: str,
    input_data: GuardrailCheckInput,
) -> GuardrailCheckResult:
    return _result(rule_name, rule_type, "pass", "Guardrail passed.", input_data, {})


def _result(
    rule_name: str,
    rule_type: str,
    severity: str,
    message: str,
    input_data: GuardrailCheckInput,
    evidence: dict[str, Any],
    *,
    review_required: bool = False,
    blocks_publish: bool = False,
) -> GuardrailCheckResult:
    return GuardrailCheckResult(
        rule_name=rule_name,
        rule_type=rule_type,
        severity=severity,
        passed=severity == "pass",
        message=message,
        subject_type=input_data.subject_type,
        subject_id=input_data.subject_id,
        evidence=evidence,
        review_required=review_required,
        blocks_publish=blocks_publish,
    )


default_guardrail_registry = build_default_guardrail_registry()
