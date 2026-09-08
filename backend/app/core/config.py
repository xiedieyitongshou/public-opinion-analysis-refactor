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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
