"""Application configuration."""

from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    app_name: str = "Public Opinion Analysis Backend"
    app_env: str = Field(default="local", alias="APP_ENV")
    debug: bool = False
    database_url: str = "sqlite:///./data/app.db"
    zhihu_access_secret: str | None = Field(default=None, alias="ZHIHU_ACCESS_SECRET")
    zhihu_api_base_url: str = Field(
        default="https://developer.zhihu.com",
        alias="ZHIHU_API_BASE_URL",
    )
    zhihu_hot_list_path: str = Field(
        default="/api/v1/content/hot_list",
        alias="ZHIHU_HOT_LIST_PATH",
    )
    zhihu_search_path: str = Field(
        default="/api/v1/content/zhihu_search",
        alias="ZHIHU_SEARCH_PATH",
    )
    zhihu_quota_path: str = Field(default="/api/v1/quota", alias="ZHIHU_QUOTA_PATH")
    zhihu_fetch_limit: int = Field(default=30, alias="ZHIHU_FETCH_LIMIT", ge=1, le=30)
    zhihu_timeout_seconds: float = Field(default=20.0, alias="ZHIHU_TIMEOUT_SECONDS", gt=0)
    weibo_rsshub_base_url: str = Field(
        default="http://localhost:1200",
        alias="WEIBO_RSSHUB_BASE_URL",
    )
    weibo_rsshub_route: str = Field(
        default="/weibo/search/hot",
        alias="WEIBO_RSSHUB_ROUTE",
    )
    weibo_rsshub_fulltext_route: str = Field(
        default="/weibo/search/hot/fulltext",
        alias="WEIBO_RSSHUB_FULLTEXT_ROUTE",
    )
    weibo_rsshub_fetch_limit: int = Field(
        default=20,
        alias="WEIBO_RSSHUB_FETCH_LIMIT",
        ge=1,
        le=50,
    )
    weibo_rsshub_timeout_seconds: float = Field(
        default=20.0,
        alias="WEIBO_RSSHUB_TIMEOUT_SECONDS",
        gt=0,
    )
    weibo_rsshub_max_retries: int = Field(
        default=2,
        alias="WEIBO_RSSHUB_MAX_RETRIES",
        ge=0,
        le=5,
    )
    weibo_rsshub_failure_threshold: int = Field(
        default=3,
        alias="WEIBO_RSSHUB_FAILURE_THRESHOLD",
        ge=1,
        le=20,
    )
    weibo_rsshub_skip_top: int = Field(
        default=1,
        alias="WEIBO_RSSHUB_SKIP_TOP",
        ge=0,
        le=10,
    )
    weibo_cli_enabled: bool = Field(default=False, alias="WEIBO_CLI_ENABLED")
    weibo_cli_command: str = Field(default="weibo-cli", alias="WEIBO_CLI_COMMAND")
    # Match the collector's maximum; only the selected RSS topics are searched.
    weibo_cli_topic_limit: int = Field(default=50, alias="WEIBO_CLI_TOPIC_LIMIT", ge=0, le=50)
    weibo_cli_search_count: int = Field(default=10, alias="WEIBO_CLI_SEARCH_COUNT", ge=1, le=20)
    weibo_cli_timeout_seconds: float = Field(
        default=30.0,
        alias="WEIBO_CLI_TIMEOUT_SECONDS",
        gt=0,
    )
    people_rss_url: str = Field(
        default="http://www.people.com.cn/rss/politics.xml",
        alias="PEOPLE_RSS_URL",
    )
    chinanews_rss_url: str = Field(
        default="https://www.chinanews.com.cn/rss/scroll-news.xml",
        alias="CHINANEWS_RSS_URL",
    )
    xinhua_rss_url: str = Field(
        default="http://www.xinhuanet.com/politics/news_politics.xml",
        alias="XINHUA_RSS_URL",
    )
    official_rss_fetch_limit: int = Field(
        default=20,
        alias="OFFICIAL_RSS_FETCH_LIMIT",
        ge=1,
        le=50,
    )
    official_rss_timeout_seconds: float = Field(
        default=20.0,
        alias="OFFICIAL_RSS_TIMEOUT_SECONDS",
        gt=0,
    )
    event_extraction_use_llm: bool = Field(
        default=False,
        alias="EVENT_EXTRACTION_USE_LLM",
    )
    event_extraction_llm_confidence_threshold: float = Field(
        default=0.65,
        alias="EVENT_EXTRACTION_LLM_CONFIDENCE_THRESHOLD",
        ge=0.0,
        le=1.0,
    )
    llm_provider: str = Field(default="deepseek", alias="LLM_PROVIDER")
    semantic_model_dir: str | None = None
    semantic_device: str = "cpu"
    semantic_cpu_threads: int = Field(default=4, ge=1, le=32)
    semantic_cache_path: str | None = None
    matching_profile: Literal["rules", "hybrid", "hybrid_rerank"] = "hybrid_rerank"
    official_search_enabled: bool = True
    official_search_cache_minutes: int = Field(default=30, ge=0, le=1440)
    official_search_limit: int = Field(default=5, ge=1, le=20)
    official_search_max_events: int = Field(default=3, ge=0, le=20)
    official_search_timeout_seconds: float = Field(default=10, gt=0, le=60)
    deepseek_api_key: str | None = Field(default=None, alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(default="https://api.deepseek.com", alias="DEEPSEEK_BASE_URL")
    deepseek_model: str = Field(default="deepseek-chat", alias="DEEPSEEK_MODEL")

    admin_token: str | None = None
    admin_cookie_secure: bool = False  # Set true behind HTTPS.
    scheduler_enabled: bool = False
    scheduler_until: datetime | None = None
    sampling_interval_minutes: int = Field(default=180, ge=120, le=180)
    job_timeout_seconds: int = Field(default=900, ge=60, le=3600)
    request_limits: dict[str, int] = Field(default_factory=lambda: {
        "zhihu_hot_list": 2, "zhihu_search": 5, "zhihu_quota": 1,
        "weibo_rsshub_hot_search": 4, "weibo_cli": 50,
        "chinanews_scroll_rss": 3, "people_politics_rss": 3, "xinhua_politics_rss": 3,
        "official_search_people": 3, "official_search_chinanews": 3,
    })
    quota_probe_enabled: bool = False
    briefing_timezone: str = "Asia/Shanghai"
    runtime_log_dir: str | None = None
    runtime_log_retention_days: int = Field(default=14, ge=2, le=365)
    briefing_artifact_dir: str = "./artifacts"
    daily_briefing_enabled: bool = False
    email_schedule_enabled: bool = False
    email_send_time: str = Field(default="08:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    email_recipients: list[str] = Field(default_factory=list)
    email_from: str | None = None
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_security: Literal["starttls", "ssl", "plain"] = "starttls"
    smtp_timeout_seconds: int = Field(default=20, ge=1, le=60)
    email_max_attempts: int = Field(default=3, ge=1, le=5)

    @model_validator(mode="after")
    def resolve_local_database(self):
        if self.scheduler_until is not None and self.scheduler_until.tzinfo is None:
            raise ValueError("SCHEDULER_UNTIL must include a timezone")
        url = make_url(self.database_url)
        if (url.get_backend_name() == "sqlite" and url.database not in {None, "", ":memory:"}
                and not Path(url.database).is_absolute()):
            path = Path(__file__).resolve().parents[2] / url.database
            self.database_url = url.set(database=str(path.resolve())).render_as_string()
        return self

    model_config = SettingsConfigDict(
        env_file=("backend/.env", ".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
