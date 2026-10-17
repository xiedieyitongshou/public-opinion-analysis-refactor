import pytest

from app.core.config import settings
from app.services.semantic_models import SemanticUnavailable
from app.services.weibo_relevance import classify_body, clean_body


@pytest.fixture
def scorer(monkeypatch):
    monkeypatch.setattr(settings, "matching_profile", "hybrid_rerank")
    class Model:
        score = 0.9
        pairs = []
        def rerank(self, pairs):
            self.pairs.extend(pairs)
            return [self.score] * len(pairs)
    model = Model()
    monkeypatch.setattr("app.services.weibo_relevance.get_semantic_models", lambda: model)
    return model


def test_hashtag_weather_is_scored_without_query(scorer):
    scorer.score = 0.01
    title = "多部门介入母子衣衫不整街头游荡"
    result = classify_body(title, f"大连今天气温23度，空气质量良。#{title}#")
    assert result[0] == "rejected"
    assert title not in scorer.pairs[0][1]


def test_synonym_without_keyword_overlap_can_pass(scorer):
    assert classify_body("多部门介入母子街头游荡", "当地成立工作专班，为流浪的母亲和孩子提供救助。")[0] == "accepted"


def test_signature_is_not_event_location(scorer):
    text = "【驾车安全注意事项】深圳将从2026年11月1日起，在全市道路实施全天禁鸣措施。（大同消防）"
    assert classify_body("深圳全市全天禁鸣喇叭5年", text)[0] == "accepted"
    assert "大同" not in scorer.pairs[0][1]


def test_comparison_is_not_hard_conflict(scorer):
    assert classify_body("深圳全市全天禁鸣喇叭5年", "以前是市内禁鸣，如今扩展全市，希望一视同仁。")[0] == "accepted"


def test_explicit_location_conflict_skips_model(scorer):
    assert classify_body("深圳全市全天禁鸣", "上海全市全天禁鸣措施开始执行")[0] == "rejected"
    assert scorer.pairs == []


def test_tag_only_is_unknown(scorer):
    assert classify_body("深圳全市全天禁鸣", "#深圳全市全天禁鸣# 支持！")[0] == "audit_only"
    assert scorer.pairs == []


@pytest.mark.parametrize("score, decision", [(0.099, "rejected"), (0.1, "audit_only"), (0.799, "audit_only"), (0.8, "accepted"), (float("nan"), "audit_only")])
def test_thresholds(scorer, score, decision):
    scorer.score = score
    assert classify_body("某地救助母子", "当地组织多个部门救助这对流浪母子。")[0] == decision


def test_model_failure_is_unknown(scorer, monkeypatch):
    def unavailable(pairs):
        raise SemanticUnavailable("missing weights")
    monkeypatch.setattr(scorer, "rerank", unavailable)
    assert classify_body("某地救助母子", "当地组织多个部门救助这对流浪母子。")[0] == "audit_only"
