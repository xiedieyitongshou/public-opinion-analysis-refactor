"""Ordinary tests use no network or model weights; real-model evaluation has a separate CLI."""

import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def offline_defaults(monkeypatch):
    monkeypatch.setattr(settings, "matching_profile", "rules")
    monkeypatch.setattr(settings, "official_search_enabled", False)
    monkeypatch.setattr(settings, "admin_token", "test-admin")
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(settings, "email_schedule_enabled", False)
    monkeypatch.setattr(settings, "quota_probe_enabled", False)


@pytest.fixture
def high_semantic_similarity(monkeypatch):
    """Guardrail unit tests supply a model score; real neural inference is tested separately."""

    class Vector:
        def __matmul__(self, other):
            return 1.0

    class Model:
        def encode(self, texts):
            return [Vector() for _ in texts]

    monkeypatch.setattr("app.services.matching_engine.get_semantic_models", lambda: Model())
