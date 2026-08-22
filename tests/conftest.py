import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Point every test at a scratch data dir with its own reports/audit log,
    while still reading the real (repo-shipped) policies/risk_rules files."""
    from app import config

    real_data_dir = config.BASE_DIR / "data"

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(config, "AUDIT_LOG_FILE", tmp_path / "audit_log.jsonl")
    monkeypatch.setattr(config, "POLICIES_FILE", real_data_dir / "policies.yaml")
    monkeypatch.setattr(config, "RISK_RULES_FILE", real_data_dir / "risk_rules.yaml")
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # Modules that imported config.* by value at import time need patching too.
    import app.reports as reports_module
    import app.tools.audit_log as audit_log_module

    monkeypatch.setattr(reports_module.config, "REPORTS_DIR", config.REPORTS_DIR)
    monkeypatch.setattr(audit_log_module.config, "AUDIT_LOG_FILE", config.AUDIT_LOG_FILE)

    # Clear lru_cache'd policy/risk-rule loaders between tests.
    from app.tools import policy_repository, risk_rules

    policy_repository._load_policies.cache_clear()
    risk_rules._load_risk_rules.cache_clear()

    yield
