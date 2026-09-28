"""Estimated model cost from token counts.

These are *estimates* for monitoring trends, not billing: the price table is
a snapshot that will drift from the provider's real prices, and unknown
models fall back to a default rate. Override with GOVERNAI_MODEL_PRICES.
"""
import json
from typing import Dict, Optional, Tuple

from app import config

# USD per 1M tokens: (input, output).
MODEL_PRICES: Dict[str, Tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
}
DEFAULT_PRICE: Tuple[float, float] = MODEL_PRICES["gpt-4o-mini"]


def _prices() -> Dict[str, Tuple[float, float]]:
    prices = dict(MODEL_PRICES)
    if config.MODEL_PRICES_JSON:
        try:
            for model, pair in json.loads(config.MODEL_PRICES_JSON).items():
                prices[model] = (float(pair[0]), float(pair[1]))
        except (ValueError, TypeError, IndexError, AttributeError):
            pass  # a malformed override must not break agent runs
    return prices


def price_for(model: Optional[str]) -> Tuple[float, float]:
    """Longest matching model-name prefix wins, so dated snapshots such as
    "gpt-4o-mini-2024-07-18" use the "gpt-4o-mini" price, not "gpt-4o"."""
    name = (model or "").lower()
    prices = _prices()
    for key in sorted(prices, key=len, reverse=True):
        if name.startswith(key):
            return prices[key]
    return DEFAULT_PRICE


def estimate_cost_usd(
    model: Optional[str], prompt_tokens: int, completion_tokens: int, total_tokens: int = 0
) -> float:
    """If the provider gave no prompt/completion split, price all tokens at
    the input rate (a lower bound) rather than reporting zero."""
    input_price, output_price = price_for(model)
    if prompt_tokens == 0 and completion_tokens == 0:
        prompt_tokens = total_tokens
    return round((prompt_tokens * input_price + completion_tokens * output_price) / 1_000_000, 6)
