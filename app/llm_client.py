"""Thin wrapper around OpenRouter's OpenAI-compatible chat completions API.

OpenRouter (https://openrouter.ai) exposes a single OpenAI-compatible
endpoint that can route to many different underlying models (OpenAI,
Anthropic, Google, Meta, etc). We use the official `openai` SDK pointed at
OpenRouter's base URL, which gives us chat completions + tool/function
calling for free.
"""
from functools import lru_cache

from openai import OpenAI

from app import config


class ConfigurationError(RuntimeError):
    """Raised when required configuration (e.g. an API key) is missing."""


@lru_cache(maxsize=1)
def get_client() -> OpenAI:
    if not config.OPENROUTER_API_KEY:
        raise ConfigurationError(
            "OPENROUTER_API_KEY is not set. Copy .env.example to .env and add "
            "your OpenRouter API key (https://openrouter.ai/keys)."
        )
    return OpenAI(
        base_url=config.OPENROUTER_BASE_URL,
        api_key=config.OPENROUTER_API_KEY,
        default_headers={
            "HTTP-Referer": config.OPENROUTER_SITE_URL,
            "X-Title": config.OPENROUTER_APP_NAME,
        },
    )
