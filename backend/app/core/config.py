"""Application configuration from ``ABP_*`` environment variables (or a git-ignored ``.env``).

Nothing secret has a default: the app refuses to start without ``ABP_JWT_SECRET``.
The Anthropic key is read by the SDK from ``ANTHROPIC_API_KEY``; without it the agents run
with the offline, rule-based reasoner.
"""

import os
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ABP_", env_file=".env", extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://abp:abp@localhost:5432/abp"

    # --- security ---
    jwt_secret: SecretStr
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    cookie_secure: bool = True
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    login_rate_limit_per_minute: int = 10
    max_failed_logins: int = 5
    lockout_minutes: int = 15

    # --- reasoning (LLM) ---
    llm_provider: Literal["auto", "claude", "offline"] = "auto"
    claude_model: str = "claude-opus-5-5"
    claude_effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    claude_fallbacks: bool = True
    # USD per million tokens, used for the per-run cost meter (Anthropic list price for the default model).
    claude_input_usd_per_mtok: float = 4.0
    claude_output_usd_per_mtok: float = 20.0

    # --- tool execution ---
    tool_max_attempts: int = 3
    tool_retry_base_seconds: float = 0.2  # exponential backoff: 0.2 s, 0.4 s, ...
    # Fault injection for demos and resilience tests: share of tool calls that fail transiently.
    chaos_failure_rate: float = Field(default=0.0, ge=0.0, le=0.9)

    # --- logging ---
    log_level: str = "INFO"
    log_json: bool = True

    @field_validator("jwt_secret")
    @classmethod
    def _secret_strength(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value()) < 32:
            raise ValueError("ABP_JWT_SECRET must be at least 32 characters")
        return v

    @property
    def llm_mode(self) -> Literal["claude", "offline"]:
        """Resolve ``auto``: Claude when an API key is configured, otherwise offline."""
        if self.llm_provider == "auto":
            return "claude" if os.environ.get("ANTHROPIC_API_KEY") else "offline"
        return self.llm_provider


@lru_cache
def get_settings() -> Settings:
    return Settings()
