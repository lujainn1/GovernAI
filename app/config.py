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

# --- OpenAI (LLM provider) --------------------------------------------------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

MAX_TOOL_ITERATIONS = int(os.environ.get("GOVERNAI_MAX_TOOL_ITERATIONS", "10"))

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
