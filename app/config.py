"""Central configuration for the GovernAI platform.

All settings are read from environment variables (optionally loaded from a
.env file) so the platform can be configured without touching code.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# --- Storage locations -------------------------------------------------------
# GovernAI persists use cases, policies, risk rules, reports, and the audit
# log in Supabase Postgres (see app/db.py + supabase/migrations). The YAML
# files below are kept only as the seed source for scripts/seed_supabase.py.
DATA_DIR = Path(os.environ.get("GOVERNAI_DATA_DIR", BASE_DIR / "data"))
POLICIES_FILE = DATA_DIR / "policies.yaml"
RISK_RULES_FILE = DATA_DIR / "risk_rules.yaml"

# The accepted SDAIA FAISS index the Policy Compliance Agent retrieves its
# evidence from (built by app/rag/vector_store.py). Unlike the YAML above this
# IS needed at runtime, so the Dockerfile copies it into the image and
# .dockerignore re-includes it - everything else under data/ stays out.
# Derived from BASE_DIR rather than the process's working directory so the
# index is found however the app is started.
VECTOR_STORE_DIR = Path(
    os.environ.get(
        "GOVERNAI_VECTOR_STORE_DIR", DATA_DIR / "vectorstore" / "sdaia_faiss"
    )
)

# --- OpenAI (LLM provider) --------------------------------------------------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

# --- Backup LLM provider (optional, off by default) -------------------------
# GovernAI uses OpenAI/GPT as its provider. DeepSeek serves an
# OpenAI-compatible chat-completions API and can stand in for it manually when
# OpenAI is unavailable; it is selected only by setting
# GOVERNAI_LLM_PROVIDER=deepseek, and nothing falls over to it automatically.
#
# The switch covers CHAT COMPLETIONS ONLY. Embeddings (app/memory.py, and the
# SDAIA retriever) stay on OpenAI, because DeepSeek serves no embeddings
# endpoint - so a DeepSeek run is not independent of OpenAI.
LLM_PROVIDER = os.environ.get("GOVERNAI_LLM_PROVIDER", "openai").strip().lower()

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
# deepseek-flash is a reasoning model and answers far more slowly than
# gpt-4o-mini, so a request is bounded rather than left to hang.
DEEPSEEK_TIMEOUT = float(os.environ.get("DEEPSEEK_TIMEOUT", "180"))
DEEPSEEK_MAX_RETRIES = int(os.environ.get("DEEPSEEK_MAX_RETRIES", "1"))

MAX_TOOL_ITERATIONS = int(os.environ.get("GOVERNAI_MAX_TOOL_ITERATIONS", "10"))

# How many times an agent may be handed its own unusable answer back to
# correct - valid JSON that dropped a required field, or output that could not
# be parsed as JSON at all. Counted separately from MAX_TOOL_ITERATIONS so a
# model that keeps answering badly can't eat the tool budget. 0 disables
# repairs: the first AgentError raises.
MAX_SCHEMA_REPAIRS = int(os.environ.get("GOVERNAI_MAX_SCHEMA_REPAIRS", "1"))

# How many times the Review Agent may send the Decision Agent back for a
# revision before the orchestrator gives up on convergence. 0 keeps the
# review (and its audit entry) but never re-runs the Decision Agent.
MAX_REVIEW_REVISIONS = int(os.environ.get("GOVERNAI_MAX_REVIEW_REVISIONS", "1"))

# --- Long-term agent memory -----------------------------------------------
# Finished cases are embedded and stored in Supabase (agent_memory table, see
# app/memory.py); before the agents run, the most similar past cases are
# handed to them as precedent. Memory is best-effort: if it can't be read or
# written the agents simply run without it.
MEMORY_ENABLED = os.environ.get("GOVERNAI_MEMORY_ENABLED", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
MEMORY_TOP_K = int(os.environ.get("GOVERNAI_MEMORY_TOP_K", "3"))
# Cosine similarity a past case must reach to be recalled (0-1, higher = stricter).
MEMORY_MIN_SIMILARITY = float(os.environ.get("GOVERNAI_MEMORY_MIN_SIMILARITY", "0.35"))
EMBEDDING_MODEL = os.environ.get("GOVERNAI_EMBEDDING_MODEL", "text-embedding-3-small")

# --- Observability --------------------------------------------------------------
# LOG_FORMAT: "json" (one JSON object per line, for log aggregators) or "text".
LOG_LEVEL = os.environ.get("GOVERNAI_LOG_LEVEL", "INFO").upper()
LOG_FORMAT = os.environ.get("GOVERNAI_LOG_FORMAT", "json").lower()
# Optional JSON object overriding the built-in model price table, in USD per
# 1M tokens as [input, output], e.g. {"gpt-4o-mini": [0.15, 0.60]}.
MODEL_PRICES_JSON = os.environ.get("GOVERNAI_MODEL_PRICES")

# --- CORS -----------------------------------------------------------------
# Comma-separated list of allowed browser origins for the frontend. Defaults
# to the local Vite dev server so `python main.py` keeps working out of the
# box; set GOVERNAI_CORS_ORIGINS in production (e.g. on Render) to the
# deployed Vercel URL(s).
CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("GOVERNAI_CORS_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]

# --- Supabase (authentication + database) ------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY")
# Service role key: server-side only, never expose to the frontend. Used by
# app/db.py to read/write Postgres via PostgREST, bypassing Row Level
# Security (the API layer already enforces auth via app.auth.get_current_user).
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
