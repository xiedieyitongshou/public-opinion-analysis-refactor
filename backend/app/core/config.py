"""Application configuration."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    weibo_cli_topic_limit: int = Field(default=3, alias="WEIBO_CLI_TOPIC_LIMIT", ge=0, le=10)
    weibo_cli_search_count: int = Field(default=5, alias="WEIBO_CLI_SEARCH_COUNT", ge=1, le=20)
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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
