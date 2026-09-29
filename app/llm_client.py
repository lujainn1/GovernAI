"""Thin wrapper around the OpenAI chat completions API.

`get_client` is the OpenAI client and stays that way: embeddings
(app/memory.py, the SDAIA retriever) have no DeepSeek equivalent.

`get_chat_client` is what the agents talk to. It is the same OpenAI client
unless GOVERNAI_LLM_PROVIDER selects the optional DeepSeek backup, which
serves an OpenAI-compatible chat API. Nothing switches automatically.
"""
from functools import lru_cache
from typing import Callable, Optional

from openai import OpenAI

from app import config


class ConfigurationError(RuntimeError):
    """Raised when required configuration (e.g. an API key) is missing."""


@lru_cache(maxsize=1)
def get_client() -> OpenAI:
    if not config.OPENAI_API_KEY:
        raise ConfigurationError(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and add "
            "your OpenAI API key (https://platform.openai.com/api-keys)."
        )
    return OpenAI(api_key=config.OPENAI_API_KEY)


@lru_cache(maxsize=2)
def _deepseek_client() -> OpenAI:
    if not (config.DEEPSEEK_API_KEY or "").strip():
        raise ConfigurationError(
            "GOVERNAI_LLM_PROVIDER=deepseek but DEEPSEEK_API_KEY is not set. Add "
            "it to .env (the same file that holds OPENAI_API_KEY), or unset "
            "GOVERNAI_LLM_PROVIDER to use OpenAI."
        )
    return OpenAI(
        api_key=config.DEEPSEEK_API_KEY.strip(),
        base_url=config.DEEPSEEK_BASE_URL,
        timeout=config.DEEPSEEK_TIMEOUT,
        max_retries=config.DEEPSEEK_MAX_RETRIES,
    )


def active_provider(provider: Optional[str] = None) -> str:
    """The chat provider in force: the argument, else GOVERNAI_LLM_PROVIDER,
    else OpenAI."""
    name = (provider or config.LLM_PROVIDER or "openai").strip().lower()
    if name not in ("openai", "deepseek"):
        raise ConfigurationError(
            f"GOVERNAI_LLM_PROVIDER={name!r} is not a known provider; use "
            "'openai' (default) or 'deepseek'."
        )
    return name


def get_chat_client(
    openai_factory: Optional[Callable[[], OpenAI]] = None,
    provider: Optional[str] = None,
) -> OpenAI:
    """The client the agents use for chat completions.

    On the default path this simply calls `openai_factory` (the caller's own
    reference to `get_client`, which tests monkeypatch) - so OpenAI behaviour,
    including every existing test seam, is untouched. The DeepSeek client is
    returned only when the backup provider is explicitly selected.
    """
    if active_provider(provider) == "deepseek":
        return _deepseek_client()
    return (openai_factory or get_client)()


def active_chat_model(provider: Optional[str] = None) -> str:
    """The model name that goes with the active chat provider."""
    return (
        config.DEEPSEEK_MODEL
        if active_provider(provider) == "deepseek"
        else config.OPENAI_MODEL
    )
