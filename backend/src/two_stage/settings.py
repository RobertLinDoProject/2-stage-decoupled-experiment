from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    app_env: str
    local_artifact_root: str
    current_project_data_root: str
    current_project_config_root: str
    live_gai_provider_enabled: bool
    gai_execution_mode: str = "reserved_unavailable"
    gai_provider_endpoint: str | None = None
    gai_provider_api_key: str | None = None
    gai_provider_name: str = "gemini"
    gai_provider_model: str = "gemini-3.1-flash-lite"
    gai_provider_model_version: str | None = None
    gai_prompt_template_version: str = "m6_gemini_decision_v1"
    gai_temperature: float | None = None
    gai_reasoning_effort: str | None = None
    gai_timeout_ms: int = 30_000
    gai_max_retries: int = 1
    gai_budget_mode: str = "manual"
    gai_budget_max_requests_per_run: int = 100
    gai_budget_hard_limit: int = 50_000
    gai_max_output_tokens: int | None = None
    gai_num_ctx: int = 2048
    gai_keep_alive: str = "5m"
    gai_seed: int = 114
    openai_api_key: str | None = None
    openai_api_endpoint: str = "https://api.openai.com/v1/responses"
    openai_model: str = "gpt-5-nano-2025-08-07"
    cors_allow_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5273",
        "http://127.0.0.1:5273",
    )


def get_settings() -> Settings:
    cors_allow_origins = tuple(
        origin.strip()
        for origin in os.getenv(
            "CORS_ALLOW_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5273,http://127.0.0.1:5273",
        ).split(",")
        if origin.strip()
    )
    provider_name = os.getenv("GAI_PROVIDER_NAME", "gemini").strip().lower()
    provider_model = os.getenv("GAI_PROVIDER_MODEL", "gemini-3.1-flash-lite")
    execution_mode = (
        os.getenv("GAI_EXECUTION_MODE", "reserved_unavailable").strip().lower()
        or "reserved_unavailable"
    )
    provider_endpoint = _optional_env("GAI_PROVIDER_ENDPOINT")
    if provider_name == "gemini" and provider_endpoint is None:
        provider_endpoint = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{provider_model}:generateContent"
        )
    if provider_name == "ollama" and provider_endpoint is None:
        provider_endpoint = "http://host.docker.internal:11434/api/chat"
    openai_endpoint = os.getenv(
        "OPENAI_API_ENDPOINT",
        "https://api.openai.com/v1/responses",
    ).strip()
    openai_model = os.getenv(
        "OPENAI_MODEL",
        "gpt-5-nano-2025-08-07",
    ).strip()
    openai_api_key = _optional_env("OPENAI_API_KEY")
    provider_api_key = (
        _optional_env("GAI_PROVIDER_API_KEY") or _optional_env("GEMINI_API_KEY")
        if execution_mode == "live" and provider_name != "ollama"
        else None
    )
    return Settings(
        app_env=os.getenv("APP_ENV", "development"),
        local_artifact_root=os.getenv("LOCAL_ARTIFACT_ROOT", "storage"),
        current_project_data_root=os.getenv("CURRENT_PROJECT_DATA_ROOT", "/data/current-project"),
        current_project_config_root=os.getenv(
            "CURRENT_PROJECT_CONFIG_ROOT",
            "/app/configs",
        ),
        live_gai_provider_enabled=_bool_env("LIVE_GAI_PROVIDER_ENABLED", default=False),
        gai_execution_mode=execution_mode,
        gai_provider_endpoint=provider_endpoint,
        gai_provider_api_key=provider_api_key,
        gai_provider_name=provider_name,
        gai_provider_model=provider_model,
        gai_provider_model_version=_optional_env("GAI_PROVIDER_MODEL_VERSION"),
        gai_prompt_template_version=os.getenv(
            "GAI_PROMPT_TEMPLATE_VERSION",
            "m6_gemini_decision_v1",
        ),
        gai_temperature=_optional_float_env("GAI_TEMPERATURE"),
        gai_reasoning_effort=_optional_env("GAI_REASONING_EFFORT"),
        gai_timeout_ms=_int_env("GAI_TIMEOUT_MS", default=30_000),
        gai_max_retries=_int_env("GAI_MAX_RETRIES", default=1),
        gai_budget_mode=(os.getenv("GAI_BUDGET_MODE", "auto").strip().lower() or "auto"),
        gai_budget_max_requests_per_run=_int_env(
            "GAI_BUDGET_MAX_REQUESTS_PER_RUN",
            default=100,
        ),
        gai_budget_hard_limit=_int_env("GAI_BUDGET_HARD_LIMIT", default=50_000),
        gai_max_output_tokens=_optional_int_env("GAI_MAX_OUTPUT_TOKENS"),
        gai_num_ctx=_int_env("GAI_NUM_CTX", default=2048),
        gai_keep_alive=os.getenv("GAI_KEEP_ALIVE", "5m").strip() or "5m",
        gai_seed=_int_env("GAI_SEED", default=114),
        openai_api_key=openai_api_key,
        openai_api_endpoint=openai_endpoint,
        openai_model=openai_model,
        cors_allow_origins=cors_allow_origins,
    )


def _optional_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    return value.strip()


def _bool_env(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, *, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def _optional_float_env(name: str) -> float | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    return float(value)


def _optional_int_env(name: str) -> int | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    return int(value)
