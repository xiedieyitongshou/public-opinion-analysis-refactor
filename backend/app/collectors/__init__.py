"""Collector runtime primitives."""

from app.collectors.base import BaseCollector, CollectorError
from app.collectors.official import (
    OfficialRSSCollector,
    OfficialRSSSourceConfig,
    default_official_collectors,
)
from app.collectors.registry import (
    CollectorAlreadyRegisteredError,
    CollectorNotFoundError,
    CollectorRegistrationError,
    CollectorRegistry,
    build_default_collector_registry,
    default_collector_registry,
)
from app.collectors.weibo import WeiboHeatCollector
from app.collectors.zhihu import ZhihuHotListCollector, ZhihuSearchCollector

__all__ = [
    "BaseCollector",
    "build_default_collector_registry",
    "CollectorAlreadyRegisteredError",
    "CollectorError",
    "CollectorNotFoundError",
    "CollectorRegistrationError",
    "CollectorRegistry",
    "default_collector_registry",
    "default_official_collectors",
    "OfficialRSSCollector",
    "OfficialRSSSourceConfig",
    "WeiboHeatCollector",
    "ZhihuHotListCollector",
    "ZhihuSearchCollector",
]
