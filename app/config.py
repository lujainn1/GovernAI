"""Central configuration for the GovernAI platform.

All settings are read from environment variables (optionally loaded from a
.env file) so the platform can be configured without touching code.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# --- Storage locations -----------------------------------------------------
DATA_DIR = Path(os.environ.get("GOVERNAI_DATA_DIR", BASE_DIR / "data"))
REPORTS_DIR = DATA_DIR / "reports"
POLICIES_FILE = DATA_DIR / "policies.yaml"
RISK_RULES_FILE = DATA_DIR / "risk_rules.yaml"
AUDIT_LOG_FILE = DATA_DIR / "audit_log.jsonl"

REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# --- OpenRouter (LLM provider) ---------------------------------------------
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
OPENROUTER_SITE_URL = os.environ.get("OPENROUTER_SITE_URL", "https://github.com/lujainn1/governai")
OPENROUTER_APP_NAME = os.environ.get("OPENROUTER_APP_NAME", "GovernAI")

MAX_TOOL_ITERATIONS = int(os.environ.get("GOVERNAI_MAX_TOOL_ITERATIONS", "5"))
