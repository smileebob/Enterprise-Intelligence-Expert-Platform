"""分环境配置（pydantic-settings，12-Factor）。

- 敏感值一律来自环境变量 / .env（规则 A-07 / B-04 / I-02）；
- 生产类必须显式提供 SECRET_KEY 与 DATABASE_URL（缺失即启动失败，fail fast）；
- 环境由 APP_ENV 选择（development / testing / production）。
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 基础
    APP_ENV: str = "development"
    APP_NAME: str = "angentplatform"
    APP_CODE: str = "angentplatform"  # 缓存 Key 前缀
    DEBUG: bool = False
    SECRET_KEY: str = "dev-only-secret-change-me"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 120  # ≤2h（规则 I-03）

    # 日志
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "text"  # text / json

    # 存储
    DATABASE_URL: str = "sqlite:///./var/app.db"

    # 缓存 / 限流存储
    CACHE_BACKEND: str = "memory"  # memory / redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # 异步任务
    CELERY_TASK_ALWAYS_EAGER: bool = True
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"

    # 模型网关（DEMO_MODE=live 时使用；OpenAI 兼容协议）
    DEMO_MODE: str = "offline"  # live / offline / replay
    LLM_BASE_URL: str = ""
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "qwen3-max"
    LLM_TIMEOUT_S: float = 120.0  # 读超时（推理型模型需更长；短超时会导致校准/生成立即降级）
    EMBED_MODEL: str = "text-embedding-v3"
    EMBED_DIM: int = 1024
    RERANK_MODEL: str = ""

    # 数据目录
    DATA_DIR: str = "data"
    MOCK_DATA_DIR: str = "data/mock"

    # 安全与限流
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:8080"]
    RATE_LIMIT_DEFAULT: str = "2000/hour"
    RATE_LIMIT_LOGIN: str = "10/minute"
    PERMISSION_CACHE_TTL_SECONDS: int = 30  # 权限缓存上限；安全边界仍以 DB 实时校验为准
    TOOL_ARGS_MAX_BYTES: int = 32768

    # MCP 受控客户端接入（03）
    MCP_ENABLED: bool = False
    MCP_RESOURCE_URL: str = "http://127.0.0.1:8000/mcp"
    MCP_TOKEN_TTL_SECONDS: int = 900
    # MCP Client：仅管理员配置的服务器；JSON 数组，凭证走环境变量名
    MCP_CLIENT_SERVERS_JSON: str = "[]"
    MCP_CLIENT_TIMEOUT_S: float = 10.0
    MCP_ALLOWED_HOSTS: list[str] = [
        "127.0.0.1",
        "127.0.0.1:*",
        "localhost",
        "localhost:*",
        "testserver",
    ]
    MCP_ALLOWED_ORIGINS: list[str] = [
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:6274",
        "http://localhost:6274",
    ]

    # 运行采集与受控载荷（05）
    RUN_EVENT_SCHEMA_VERSION: str = "1"
    RUN_PAYLOAD_RETENTION_DAYS: int = 7
    RUN_EVENT_PAGE_MAX: int = 100
    APPROVAL_TTL_SECONDS: int = 300

    # 长上下文与工具输出治理（04）
    TOOL_OUTPUT_INLINE_BYTES: int = 16384
    CONTEXT_MAX_BYTES: int = 65536
    ORCH_HISTORY_MAX_BYTES: int = 16384

    # 可观测
    METRICS_ENABLED: bool = True

    # 检索与编排参数（技术方案 §4.3 / §3.2）
    HYBRID_VECTOR_WEIGHT: float = 0.6
    HYBRID_LEXICAL_WEIGHT: float = 0.4
    RETRIEVAL_K_INTERNAL: int = 6
    RETRIEVAL_K_EXTERNAL: int = 6
    RETRIEVAL_K_CONFIRMED: int = 2  # 已审核案例受控预算（06-B）
    RETRIEVAL_SUFFICIENT_MIN: int = 4
    ORCH_MAX_REPLAN: int = 2
    ORCH_HISTORY_WINDOW: int = 10  # 会话滑窗：注入编排上下文的最近消息条数（多轮指代继承）
    CONFIDENCE_THRESHOLD: float = 0.6  # BR-10
    IDEMPOTENCY_TTL_SECONDS: int = 600

    # 种子账号口令（仅演示）
    SEED_ADMIN_PASSWORD: str = "Admin@12345"
    SEED_EXPERT_PASSWORD: str = "Expert@12345"
    SEED_KB_PASSWORD: str = "Kb@12345"
    SEED_AUDIT_PASSWORD: str = "Audit@12345"


class DevelopmentConfig(BaseConfig):
    DEBUG: bool = True


class TestingConfig(BaseConfig):
    # 测试环境强制隔离：不读 .env（避免开发配置泄漏进测试）
    model_config = SettingsConfigDict(env_file=None, env_file_encoding="utf-8", extra="ignore")

    APP_ENV: str = "testing"
    DEBUG: bool = False
    DATABASE_URL: str = "sqlite://"
    CACHE_BACKEND: str = "memory"
    CELERY_TASK_ALWAYS_EAGER: bool = True
    METRICS_ENABLED: bool = False
    DEMO_MODE: str = "offline"
    LOG_FORMAT: str = "text"
    LOG_LEVEL: str = "WARNING"

    def __init__(self, **data):
        super().__init__(**data)
        # 防环境变量/外部来源覆盖导致测试不隔离
        self.DATABASE_URL = "sqlite://"
        self.CACHE_BACKEND = "memory"
        self.CELERY_TASK_ALWAYS_EAGER = True
        self.METRICS_ENABLED = False
        self.DEMO_MODE = "offline"


class ProductionConfig(BaseConfig):
    DEBUG: bool = False
    APP_ENV: str = "production"
    LOG_FORMAT: str = "json"
    # 生产必须显式注入（无默认值 → 缺失即启动失败）
    SECRET_KEY: str
    DATABASE_URL: str


_CONFIGS: dict[str, type[BaseConfig]] = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


@lru_cache(maxsize=8)
def _cached_config(env: str) -> BaseConfig:
    if env not in _CONFIGS:
        raise RuntimeError(f"未知 APP_ENV: {env}，可选 {sorted(_CONFIGS)}")
    return _CONFIGS[env]()


def get_settings(env: str | None = None) -> BaseConfig:
    """获取配置实例（按 env 缓存）。env 缺省读 APP_ENV 环境变量。"""
    return _cached_config(env or os.environ.get("APP_ENV", "development"))


def reset_settings_cache() -> None:
    """测试用：清空配置缓存。"""
    _cached_config.cache_clear()
