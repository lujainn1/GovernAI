"""One-time migration: load the policies and risk rules that used to live in
data/policies.yaml and data/risk_rules.yaml into Supabase.

Run this once, after applying supabase/migrations/0001_init_schema.sql and
setting SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY in your environment:

    python -m scripts.seed_supabase

Safe to re-run: rows that already exist (matched by id) are skipped.
"""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config
from app.tools.policy_repository import add_policy
from app.tools.risk_rules import add_risk_rule


def seed_policies() -> None:
    data = yaml.safe_load(config.POLICIES_FILE.read_text(encoding="utf-8")) or {}
    for policy in data.get("policies", []):
        try:
            add_policy(policy)
            print(f"  + policy {policy['id']}")
        except ValueError:
            print(f"  = policy {policy['id']} already exists, skipping")


def seed_risk_rules() -> None:
    data = yaml.safe_load(config.RISK_RULES_FILE.read_text(encoding="utf-8")) or {}
    for rule in data.get("risk_rules", []):
        try:
            add_risk_rule(rule)
            print(f"  + risk rule {rule['id']}")
        except ValueError:
            print(f"  = risk rule {rule['id']} already exists, skipping")


if __name__ == "__main__":
    print("Seeding policies...")
    seed_policies()
    print("Seeding risk rules...")
    seed_risk_rules()
    print("Done.")
