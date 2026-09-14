"""Rule-first EventSignal extraction for Day 38."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import settings
from app.schemas import (
    EventSignal,
    ExtractEventSignalsInput,
    ExtractEventSignalsOutput,
    SemanticFingerprint,
    SourceSignal,
)

HASHTAG_RE = re.compile(r"#([^#\s]+)#?")
QUOTED_RE = re.compile(r"[《「『“\"']([^》」』”\"']{2,30})[》」』”\"']")
URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
CHINESE_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
TOKEN_SPLIT_RE = re.compile(r"[\s,，。.!！？?;；:：、/\\|()\[\]{}<>《》「」『』“”\"'`~]+")
REPEATED_PUNCT_RE = re.compile(r"([!?！？。.,，、])\1+")

GENERIC_KEYWORDS = {
    "最新",
    "突发",
    "热搜",
    "官方",
    "网友",
    "视频",
    "现场",
    "如何看待",
    "怎么回事",
    "一男子",
    "一女子",
    "多人",
    "多地",
    "回应",
    "通报",
}

ACTION_TERMS = (
    "回应",
    "通报",
    "发布",
    "曝光",
    "举报",
    "调查",
    "处罚",
    "起诉",
    "判决",
    "道歉",
    "辟谣",
    "下架",
    "召回",
    "暂停",
    "恢复",
    "涉嫌",
    "发生",
    "引发",
    "确认",
    "否认",
    "约谈",
    "立案",
    "抓获",
    "救援",
)

EVENT_TYPE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("public_safety", ("事故", "火灾", "地震", "救援", "伤亡", "警方")),
    ("legal_case", ("起诉", "判决", "立案", "抓获", "涉嫌", "法院")),
    ("consumer_rights", ("投诉", "召回", "下架", "维权", "消费者", "质量")),
    ("policy_notice", ("政策", "通知", "规定", "办法", "发布", "调整")),
    ("education", ("学校", "高考", "中考", "学生", "教师", "教育")),
    ("health", ("医院", "医生", "疾病", "感染", "疫苗", "药品")),
    ("public_issue", ("回应", "通报", "网友", "引发", "关注", "争议")),
)

ENTITY_MARKERS = (
    "公司",
    "大学",
    "学院",
    "医院",
    "学校",
    "平台",
    "集团",
    "警方",
    "法院",
    "市",
    "省",
)


class EventExtractionRefinement(BaseModel):
    """Allowed LLM refinement shape; never a full EventSignal."""

    model_config = ConfigDict(extra="forbid")

    normalized_topic: str | None = None
    keywords: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    event_type: str | None = None
    action_terms: list[str] = Field(default_factory=list)
    is_weak_signal: bool | None = None
    weak_signal_reason: str | None = None
    confidence_delta: float = Field(default=0.0, ge=-0.3, le=0.3)


@dataclass
class RuleExtractionResult:
    signal: SourceSignal
    keywords: list[str]
    entities: list[str]
    action_terms: list[str]
    event_type: str | None
    event_time_hint: datetime | str | None
    confidence: float
    is_weak_signal: bool
    weak_signal_reason: str | None
    quality_flags: list[str]


class DeepSeekEventExtractionClient:
    """Adapter placeholder for future OpenAI-compatible DeepSeek calls."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self.api_key = api_key or settings.deepseek_api_key
        self.base_url = base_url or settings.deepseek_base_url
        self.model = model or settings.deepseek_model

    def refine(self, prompt_payload: dict[str, Any]) -> EventExtractionRefinement:
        raise RuntimeError("DeepSeek event extraction refinement is not implemented")


class LLMEventExtractionRefiner:
    """Controlled LLM refinement wrapper.

    The default client is a non-calling placeholder. This keeps the Day 38
    implementation deterministic while preserving the provider boundary.
    """

    def __init__(self, client: DeepSeekEventExtractionClient | None = None) -> None:
        self.client = client or DeepSeekEventExtractionClient()

    def refine(self, result: RuleExtractionResult) -> EventExtractionRefinement:
        payload = {
            "title": result.signal.title,
            "platform": result.signal.platform,
            "signal_role": result.signal.signal_role,
            "published_at": result.signal.published_at,
            "rule_keywords": result.keywords,
            "rule_entities": result.entities,
            "rule_action_terms": result.action_terms,
            "rule_confidence": result.confidence,
            "quality_flags": result.quality_flags,
        }
        return self.client.refine(payload)


class RuleEventExtractor:
    """Deterministic baseline extractor shared by all source types."""

    def extract(self, signal: SourceSignal) -> RuleExtractionResult:
        title = signal.title.strip()
        field_terms = _extract_platform_terms(signal)
        phrases = _extract_phrases(title)
        actions = _extract_actions(title)
        entities = _extract_entities(title, phrases, signal)
        keywords = _extract_keywords(title, field_terms=field_terms, phrases=phrases)
        event_type = _classify_event_type(title, actions)
        weak_reason = _weak_signal_reason(
            title=title,
            signal=signal,
            entities=entities,
            actions=actions,
            keywords=keywords,
        )
        quality_flags = list(signal.quality_flags)
        if weak_reason:
            quality_flags.append(weak_reason)
        confidence = _score_confidence(
            signal=signal,
            title=title,
            entities=entities,
            actions=actions,
            event_time_hint=signal.published_at,
            quality_flags=quality_flags,
        )
        return RuleExtractionResult(
            signal=signal,
            keywords=keywords,
            entities=entities,
            action_terms=actions,
            event_type=event_type,
            event_time_hint=signal.published_at,
            confidence=confidence,
            is_weak_signal=weak_reason is not None,
            weak_signal_reason=weak_reason,
            quality_flags=_dedupe(quality_flags),
        )


class EventSignalAssembler:
    """Build strict EventSignal objects from rule/refined extraction results."""

    def assemble(
        self,
        *,
        run_id: str,
        result: RuleExtractionResult,
        refinement: EventExtractionRefinement | None = None,
    ) -> EventSignal:
        keywords = result.keywords
        entities = result.entities
        action_terms = result.action_terms
        event_type = result.event_type
        is_weak_signal = result.is_weak_signal
        weak_signal_reason = result.weak_signal_reason
        confidence = result.confidence
        title = result.signal.title.strip()

        if refinement is not None:
            if refinement.normalized_topic:
                title = refinement.normalized_topic.strip()
            keywords = _limit_terms([*refinement.keywords, *keywords], limit=8)
            entities = _limit_terms([*refinement.entities, *entities], limit=8)
            action_terms = _limit_terms([*refinement.action_terms, *action_terms], limit=6)
            event_type = refinement.event_type or event_type
            if refinement.is_weak_signal is not None:
                is_weak_signal = refinement.is_weak_signal
            weak_signal_reason = refinement.weak_signal_reason or weak_signal_reason
            confidence = _clip(confidence + refinement.confidence_delta)

        if is_weak_signal and not weak_signal_reason:
            weak_signal_reason = "short_or_generic_topic_without_enough_event_constraints"

        match_text = _build_event_text_for_match(
            title=title,
            keywords=keywords,
            entities=entities,
            action_terms=action_terms,
            event_type=event_type,
            event_time_hint=result.event_time_hint,
        )
        return EventSignal(
            event_signal_id=_event_signal_id(run_id, result.signal.source_signal_id),
            source_signal_ids=[result.signal.source_signal_id],
            title=title,
            keywords=keywords,
            entities=entities,
            event_type=event_type,
            action_terms=action_terms,
            event_time_hint=result.event_time_hint,
            event_text_for_match=match_text,
            semantic_fingerprint=SemanticFingerprint(
                event_text_for_embedding=_build_event_text_for_embedding(match_text)
            ),
            confidence=confidence,
            is_weak_signal=is_weak_signal,
            weak_signal_reason=weak_signal_reason,
            source_statuses=[result.signal.source_status],
            source_roles=[result.signal.signal_role],
            platforms=[result.signal.platform],
            quality_flags=result.quality_flags,
        )


class EventSignalExtractor:
    """Day 38 extraction tool implementation."""

    def __init__(
        self,
        *,
        rule_extractor: RuleEventExtractor | None = None,
        refiner: LLMEventExtractionRefiner | None = None,
        assembler: EventSignalAssembler | None = None,
        use_llm_globally: bool | None = None,
        llm_confidence_threshold: float | None = None,
    ) -> None:
        self.rule_extractor = rule_extractor or RuleEventExtractor()
        self.refiner = refiner or LLMEventExtractionRefiner()
        self.assembler = assembler or EventSignalAssembler()
        self.use_llm_globally = (
            settings.event_extraction_use_llm if use_llm_globally is None else use_llm_globally
        )
        self.llm_confidence_threshold = (
            settings.event_extraction_llm_confidence_threshold
            if llm_confidence_threshold is None
            else llm_confidence_threshold
        )

    def extract(self, input_data: ExtractEventSignalsInput) -> ExtractEventSignalsOutput:
        if not input_data.source_signals:
            return ExtractEventSignalsOutput(
                run_id=input_data.run_id,
                status="skipped",
                quality_flags=["empty_source_signals"],
            )

        event_signals: list[EventSignal] = []
        errors: list[str] = []
        quality_flags: list[str] = []
        audit_only_ids: list[str] = []
        skipped_count = 0

        for signal in input_data.source_signals:
            if signal.audit_only:
                audit_only_ids.append(signal.source_signal_id)
                skipped_count += 1
                continue
            if not signal.contributes_to_classification:
                quality_flags.append("skipped_non_contributing_source_signal")
                skipped_count += 1
                continue
            if not signal.title.strip():
                errors.append(f"{signal.source_signal_id}: missing title")
                continue

            try:
                result = self.rule_extractor.extract(signal)
                refinement = None
                if self._should_use_llm(input_data=input_data, result=result):
                    try:
                        refinement = self.refiner.refine(result)
                        quality_flags.append("llm_refinement_used")
                    except (RuntimeError, TimeoutError, ValidationError, ValueError) as exc:
                        errors.append(f"{signal.source_signal_id}: llm refinement failed: {exc}")
                        quality_flags.append("llm_refinement_failed")
                event_signals.append(
                    self.assembler.assemble(
                        run_id=input_data.run_id,
                        result=result,
                        refinement=refinement,
                    )
                )
            except (ValidationError, ValueError, TypeError) as exc:
                errors.append(f"{signal.source_signal_id}: {exc}")

        if event_signals:
            status = "partial" if errors or skipped_count else "succeeded"
        elif skipped_count == len(input_data.source_signals) and not errors:
            status = "skipped"
        else:
            status = "failed"

        return ExtractEventSignalsOutput(
            run_id=input_data.run_id,
            status=status,
            event_signals=event_signals,
            audit_only_source_signal_ids=audit_only_ids,
            quality_flags=_dedupe(quality_flags),
            errors=errors,
        )

    def _should_use_llm(
        self,
        *,
        input_data: ExtractEventSignalsInput,
        result: RuleExtractionResult,
    ) -> bool:
        if not self.use_llm_globally or not input_data.use_llm:
            return False
        return (
            result.confidence < self.llm_confidence_threshold
            or not result.entities
            or not result.action_terms
            or result.is_weak_signal
            or _is_high_value_signal(result.signal)
        )


def _extract_phrases(title: str) -> list[str]:
    return _dedupe(
        [
            *[match.group(1).strip() for match in HASHTAG_RE.finditer(title)],
            *[match.group(1).strip() for match in QUOTED_RE.finditer(title)],
        ]
    )


def _extract_platform_terms(signal: SourceSignal) -> list[str]:
    terms: list[str] = []
    for container in (signal.raw_metrics, signal.platform_features, signal.classification_features):
        terms.extend(_terms_from_mapping(container))
    return _dedupe(terms)


def _terms_from_mapping(value: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for key, item in value.items():
        lowered = key.lower()
        if not any(token in lowered for token in ("topic", "query", "keyword", "tag")):
            continue
        if isinstance(item, list | tuple | set):
            terms.extend(str(part).strip("# ") for part in item if str(part).strip("# "))
        elif item:
            terms.append(str(item).strip("# "))
    return terms


def _extract_keywords(title: str, *, field_terms: list[str], phrases: list[str]) -> list[str]:
    candidates: list[str] = []
    candidates.extend(phrases)
    candidates.extend(field_terms)
    candidates.extend(part for part in TOKEN_SPLIT_RE.split(title) if part)
    for segment in CHINESE_RE.findall(title):
        if 2 <= len(segment) <= 8:
            candidates.append(segment)
        for size in (2, 3, 4):
            candidates.extend(
                segment[index : index + size] for index in range(len(segment) - size + 1)
            )
    filtered = [
        term
        for term in (_normalize_term(candidate) for candidate in candidates)
        if term and not _is_generic_keyword(term) and term not in ACTION_TERMS
    ]
    return _limit_terms(filtered, limit=8)


def _extract_entities(title: str, phrases: list[str], signal: SourceSignal) -> list[str]:
    entities: list[str] = []
    entities.extend(phrase for phrase in phrases if len(phrase) >= 2)
    for segment in CHINESE_RE.findall(title):
        if any(marker in segment for marker in ENTITY_MARKERS):
            entities.append(_trim_entity_segment(segment))
    if signal.platform in {"weibo", "zhihu", "bilibili"}:
        entities.append(signal.platform)
    return _limit_terms(entities, limit=8)


def _trim_entity_segment(segment: str) -> str:
    if len(segment) <= 12:
        return segment
    for marker in ENTITY_MARKERS:
        index = segment.find(marker)
        if index >= 0:
            return segment[max(0, index - 8) : index + len(marker)]
    return segment[:12]


def _extract_actions(title: str) -> list[str]:
    return [term for term in ACTION_TERMS if term in title]


def _classify_event_type(title: str, actions: list[str]) -> str | None:
    text = " ".join([title, *actions])
    for event_type, terms in EVENT_TYPE_RULES:
        if any(term in text for term in terms):
            return event_type
    return None


def _weak_signal_reason(
    *,
    title: str,
    signal: SourceSignal,
    entities: list[str],
    actions: list[str],
    keywords: list[str],
) -> str | None:
    title_without_hash = HASHTAG_RE.sub(r"\1", title).strip()
    only_hashtag = bool(HASHTAG_RE.fullmatch(title.strip()))
    short_or_generic = len(title_without_hash) < 6 or len(keywords) < 3
    constraints = sum(
        [
            bool(entities),
            bool(actions),
            len(keywords) >= 3,
            signal.published_at is not None,
        ]
    )
    if signal.signal_role == "topic_discovery_signal" and not entities and not actions:
        return "topic_discovery_signal_without_event_constraints"
    if only_hashtag or (short_or_generic and constraints < 2):
        return "short_or_generic_topic_without_enough_event_constraints"
    if signal.source_type == "community_video_hotlist" and constraints < 2:
        return "video_title_may_not_represent_event_fact"
    return None


def _score_confidence(
    *,
    signal: SourceSignal,
    title: str,
    entities: list[str],
    actions: list[str],
    event_time_hint: datetime | str | None,
    quality_flags: list[str],
) -> float:
    score = 0.35
    if len(title) >= 8:
        score += 0.20
    if entities:
        score += 0.15
    if actions:
        score += 0.15
    if event_time_hint is not None:
        score += 0.10
    if signal.source_status == "use":
        score += 0.10
    if any(flag in quality_flags for flag in ("low_related_search_result", "audit_only")):
        score -= 0.15
    if len(title) < 6 or any(_is_generic_keyword(term) for term in TOKEN_SPLIT_RE.split(title)):
        score -= 0.10
    return _clip(score)


def _build_event_text_for_match(
    *,
    title: str,
    keywords: list[str],
    entities: list[str],
    action_terms: list[str],
    event_type: str | None,
    event_time_hint: datetime | str | None,
) -> str:
    date_hint: str | None = None
    if isinstance(event_time_hint, datetime):
        date_hint = event_time_hint.date().isoformat()
    elif event_time_hint:
        date_hint = str(event_time_hint).split("T", 1)[0]
    return _join_text([title, *keywords, *entities, *action_terms, event_type, date_hint])


def _build_event_text_for_embedding(value: str) -> str:
    text = URL_RE.sub(" ", value)
    text = HASHTAG_RE.sub(r"\1", text)
    text = REPEATED_PUNCT_RE.sub(r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def _event_signal_id(run_id: str, source_signal_id: str) -> str:
    digest = hashlib.sha256(f"{run_id}:{source_signal_id}".encode()).hexdigest()[:16]
    return f"event-sig-{digest}"


def _is_high_value_signal(signal: SourceSignal) -> bool:
    for container in (signal.raw_metrics, signal.platform_features, signal.classification_features):
        rank = container.get("rank") or container.get("platform_rank")
        if isinstance(rank, int | float) and rank <= 10:
            return True
        bucket = container.get("platform_bucket")
        if isinstance(bucket, str) and bucket.lower() in {"top", "topn", "head"}:
            return True
    return False


def _is_generic_keyword(term: str) -> bool:
    return term in GENERIC_KEYWORDS or any(generic in term for generic in GENERIC_KEYWORDS)


def _normalize_term(value: str) -> str:
    return value.strip(" #，。！？!?;；:：、/\\|()[]{}<>《》「」『』“”\"'`~")


def _limit_terms(values: list[str], *, limit: int) -> list[str]:
    return _dedupe([_normalize_term(value) for value in values if _normalize_term(value)])[:limit]


def _join_text(parts: list[Any]) -> str:
    return re.sub(r"\s+", " ", " ".join(str(part).strip() for part in parts if part)).strip()


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _clip(value: float) -> float:
    return min(1.0, max(0.0, round(value, 4)))
