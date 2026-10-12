"""Pins railway.cron.toml's restart policy (owner decision 2026-10-12,
D-M2 review NB-1). Since D-M1/D-M2 the cron lanes return 1 on real
failures; with Railway's default policy a retry could re-send the failure
email and re-pay thesis-lane LLM calls. "never" makes one run = one email.
"""
import tomllib
from pathlib import Path

CRON_TOML = Path(__file__).resolve().parent.parent / "railway.cron.toml"


def test_cron_services_never_auto_restart():
    cfg = tomllib.loads(CRON_TOML.read_text(encoding="utf-8"))
    assert cfg["deploy"]["restartPolicyType"] == "never"


def test_cron_start_command_unchanged():
    cfg = tomllib.loads(CRON_TOML.read_text(encoding="utf-8"))
    assert cfg["deploy"]["startCommand"] == "python cron_runner.py"
