"""Pins railway.cron.toml's restart policy (owner decision 2026-10-12,
D-M2 review NB-1). Railway does not restart cron-scheduled services in any
case (verified in the dashboard 2026-10-12), so this records intent: since
D-M1/D-M2 the lanes return 1 on real failures, and one failed run must mean
one failure email, never a retry. Pinned so the intent can't drift if the
config is reused for a non-cron service.
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
