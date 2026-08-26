"""Thin wrapper around the OpenAI chat completions API.

We use the official `openai` SDK directly, which gives us chat completions
+ tool/function calling for free.
"""
from functools import lru_cache

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
