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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
