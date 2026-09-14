from pathlib import Path

from app.core.config import Settings


def test_deepseek_api_key_can_be_loaded_from_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                'LLM_PROVIDER="deepseek"',
                'DEEPSEEK_API_KEY="test-deepseek-key"',
                'DEEPSEEK_BASE_URL="https://api.deepseek.com"',
                'DEEPSEEK_MODEL="deepseek-chat"',
                "EVENT_EXTRACTION_USE_LLM=true",
                "EVENT_EXTRACTION_LLM_CONFIDENCE_THRESHOLD=0.7",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.llm_provider == "deepseek"
    assert settings.deepseek_api_key == "test-deepseek-key"
    assert settings.deepseek_base_url == "https://api.deepseek.com"
    assert settings.deepseek_model == "deepseek-chat"
    assert settings.event_extraction_use_llm is True
    assert settings.event_extraction_llm_confidence_threshold == 0.7
