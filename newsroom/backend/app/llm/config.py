from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, TypeAlias, cast


ModelTier: TypeAlias = Literal["fast", "smart"]
ProviderName: TypeAlias = Literal["deepseek", "qwen", "kimi", "glm", "claude"]

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_ROOT.parent


@dataclass(frozen=True)
class ProviderDefaults:
    base_url: str
    fast_model: str
    smart_model: str


PROVIDER_DEFAULTS: dict[ProviderName, ProviderDefaults] = {
    "deepseek": ProviderDefaults(
        base_url="https://api.deepseek.com",
        fast_model="deepseek-v4-pro",
        smart_model="deepseek-v4-pro",
    ),
    "qwen": ProviderDefaults(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        fast_model="qwen-turbo",
        smart_model="qwen-plus",
    ),
    "kimi": ProviderDefaults(
        base_url="https://api.moonshot.cn/v1",
        fast_model="moonshot-v1-8k",
        smart_model="moonshot-v1-32k",
    ),
    "glm": ProviderDefaults(
        base_url="https://open.bigmodel.cn/api/paas/v4",
        fast_model="glm-4-flash",
        smart_model="glm-4-plus",
    ),
    "claude": ProviderDefaults(
        base_url="https://api.anthropic.com/v1",
        fast_model="claude-haiku-4-5",
        smart_model="claude-sonnet-4-5",
    ),
}


@dataclass(frozen=True)
class LLMConfig:
    provider: ProviderName
    base_url: str
    api_key: str
    models: Mapping[ModelTier, str]
    timeout_seconds: float
    log_dir: Path
    thinking_type: str | None = None
    reasoning_effort: str | None = None

    def model_for(self, tier: ModelTier) -> str:
        return self.models[tier]


def read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def load_config(env_file: Path | None = None) -> LLMConfig:
    file_values = read_env_file(env_file or BACKEND_ROOT / ".env")

    def env(name: str, default: str = "") -> str:
        return os.environ.get(name, file_values.get(name, default))

    provider_value = env("LLM_PROVIDER", "deepseek").lower()
    if provider_value not in PROVIDER_DEFAULTS:
        supported = ", ".join(PROVIDER_DEFAULTS)
        raise ValueError(f"Unsupported LLM_PROVIDER {provider_value!r}; choose one of: {supported}")

    provider = cast(ProviderName, provider_value)
    defaults = PROVIDER_DEFAULTS[provider]
    prefix = provider.upper()
    api_key = env("LLM_API_KEY") or env(f"{prefix}_API_KEY")
    if not api_key:
        raise ValueError(f"Missing API key: set LLM_API_KEY or {prefix}_API_KEY")

    base_url = env("LLM_BASE_URL") or env(f"{prefix}_BASE_URL", defaults.base_url)
    fast_model = env("LLM_FAST_MODEL") or env(f"{prefix}_FAST_MODEL", defaults.fast_model)
    smart_model = env("LLM_SMART_MODEL") or env(f"{prefix}_SMART_MODEL", defaults.smart_model)
    timeout_seconds = float(env("LLM_TIMEOUT_SECONDS", "60"))
    thinking_type = env("LLM_THINKING_TYPE").strip() or None
    reasoning_effort = env("LLM_REASONING_EFFORT").strip() or None
    log_dir_value = env("LLM_LOG_DIR")
    log_dir = Path(log_dir_value) if log_dir_value else PROJECT_ROOT / "docs" / "llm-calls"

    return LLMConfig(
        provider=provider,
        base_url=base_url.rstrip("/"),
        api_key=api_key,
        models={"fast": fast_model, "smart": smart_model},
        timeout_seconds=timeout_seconds,
        log_dir=log_dir,
        thinking_type=thinking_type,
        reasoning_effort=reasoning_effort,
    )
