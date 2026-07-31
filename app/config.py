import json
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    # --- App ---
    app_env: str = 'development'
    app_name: str = 'D&S Helpdesk Assistant'
    public_base_url: str = 'http://localhost:8000'
    log_level: str = 'INFO'
    log_format: str = 'json'  # json | plain

    # --- Auth ---
    webhook_shared_secret: str = Field(min_length=16)
    cliq_signing_key: str | None = None  # X-Cliq-Signature verification (extensions only)
    allowed_origins: list[str] = Field(default_factory=list)

    # --- Freshservice ---
    freshservice_domain: str
    freshservice_api_key: str
    freshservice_workspace_id: int | None = None
    default_ticket_priority: int = 1
    default_ticket_status: int = 2
    default_ticket_source: int = 2
    kb_result_limit: int = 3
    request_timeout_seconds: float = 20.0
    freshservice_max_retries: int = 3
    freshservice_retry_base_delay: float = 0.5

    # --- Ticket routing: {"keyword": {"group_id": 1, "category": "Network"}} ---
    ticket_routing_rules: dict = Field(default_factory=dict)

    # --- Cliq outbound (optional, for async / proactive messages) ---
    cliq_bot_unique_name: str | None = None
    cliq_webhook_token: str | None = None
    cliq_dc: str = 'com'  # zoho data center: com, eu, in, com.cn, com.au, jp

    # --- Conversation sessions (multi-turn ticket creation) ---
    session_backend: str = 'memory'  # memory | redis
    redis_url: str | None = None
    session_ttl_seconds: int = 600

    # --- Rate limiting ---
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 20
    rate_limit_window_seconds: int = 60

    # --- Escalation ---
    no_kb_result_streak_before_escalation: int = 2
    escalation_group_id: int | None = None

    @field_validator('ticket_routing_rules', mode='before')
    @classmethod
    def _parse_routing_rules(cls, value):
        if isinstance(value, str):
            return json.loads(value) if value.strip() else {}
        return value or {}

    @field_validator('allowed_origins', mode='before')
    @classmethod
    def _parse_origins(cls, value):
        if isinstance(value, str):
            return [v.strip() for v in value.split(',') if v.strip()]
        return value or []

    @property
    def freshservice_base_url(self) -> str:
        domain = self.freshservice_domain.removeprefix('https://').removeprefix('http://').rstrip('/')
        return f'https://{domain}'

    @property
    def cliq_api_base_url(self) -> str:
        return f'https://cliq.zoho.{self.cliq_dc}/api/v2'

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {'prod', 'production'}


@lru_cache
def get_settings() -> Settings:
    return Settings()
