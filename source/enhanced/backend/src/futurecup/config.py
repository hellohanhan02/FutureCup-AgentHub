"""Process environment only; no filesystem or network side effects."""

from dataclasses import dataclass, field
import os


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    database_url: str
    enable_real_llm: bool = False
    llm_base_url: str | None = None
    llm_api_key_env: str = "FUTURECUP_LLM_API_KEY"
    provider_config_json: str | None = field(default=None, repr=False)
    llm_allowed_hosts: tuple[str, ...] = ()
    real_call_safety_json: str | None = field(default=None, repr=False)

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            environment=os.getenv("FUTURECUP_ENV", "development"),
            database_url=os.getenv("FUTURECUP_DATABASE_URL", "sqlite:///.data/futurecup.sqlite3"),
            enable_real_llm=os.getenv("FUTURECUP_ENABLE_REAL_LLM", "false").lower() == "true",
            llm_base_url=os.getenv("FUTURECUP_LLM_BASE_URL"),
            llm_api_key_env=os.getenv("FUTURECUP_LLM_API_KEY_ENV", "FUTURECUP_LLM_API_KEY"),
            provider_config_json=os.getenv("FUTURECUP_LLM_PROVIDERS_JSON"),
            llm_allowed_hosts=tuple(filter(None, (s.strip() for s in os.getenv("FUTURECUP_LLM_ALLOWED_HOSTS", "").split(",")))),
            real_call_safety_json=os.getenv("FUTURECUP_REAL_CALL_SAFETY_JSON"),
        )
