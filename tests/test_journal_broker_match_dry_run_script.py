"""scripts/journal_broker_match_dry_run.py (TJDate C5c): read-only, set_readonly first,
prints coverage, counts per kind / severity, every finding and the Phase-2 gate count."""
import importlib.util
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from stock_analyzer import journal_broker_match as jbm
from stock_analyzer.market_time import ET
from tests.test_journal_broker_match import _444_rows, frame, run

pytestmark = pytest.mark.fast

_SPEC = importlib.util.spec_from_file_location(
    "journal_broker_match_dry_run",
    Path(__file__).resolve().parent.parent / "scripts" / "journal_broker_match_dry_run.py")
S = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(S)

NOW = ET.localize(datetime(2026, 11, 12, 12, 0))


def _report(findings=None):
    j, f = _444_rows()
    res = run(j, f) if findings is None else findings
    return "\n".join(S.format_report(res, jbm.match_coverage(NOW)))


def test_report_shows_coverage_counts_findings_and_the_gate_count():
    out = _report()
    assert out.startswith("Coverage: Journal rows dated after")
    assert "Findings: 4" in out
    assert "differs: 4 (SPLIT 3, UNLOGGED 1)" in out
    assert "[ 30] UNLOGGED  GOOGL BUY  severity=differs" in out
    assert "[ 90] SPLIT  GOOGL BUY" in out
    assert "#444 (2026-10-29)" in out and "GO1 [pending, session 2026-10-28, placed 2026-10-28]" in out
    assert "one_click=yes" in out and "needs the 'Filled' date confirmed" in out
    assert 'Phase-2 gate count (severity "differs"): 4' in out
    assert "$" not in out.replace('"differs"', "")


def test_gate_count_counts_only_differs():
    from tests.test_journal_broker_match import _csco
    out = "\n".join(S.format_report(run(*_csco("c")), jbm.match_coverage(NOW)))
    assert "info: 2 (LINK 2)" in out
    assert 'Phase-2 gate count (severity "differs"): 0' in out


def test_none_result_is_never_printed_as_clean():
    out = "\n".join(S.format_report(None, jbm.match_coverage(None)))
    assert "couldn't check" in out and "NOT 'no differences'" in out
    assert "Phase-2 gate count" not in out and "Findings:" not in out
    assert "couldn't be read" in out.splitlines()[0]


def test_empty_result_points_back_at_the_coverage_line():
    out = "\n".join(S.format_report([], jbm.match_coverage(NOW)))
    assert "Findings: 0" in out and "read it together with the coverage line" in out
    assert 'Phase-2 gate count (severity "differs"): 0' in out


def test_main_sets_readonly_first_and_only_reads(monkeypatch, capsys):
    calls = []
    j, f = _444_rows()
    monkeypatch.setenv("SUPABASE_URL", "x")
    monkeypatch.setenv("SUPABASE_KEY", "y")
    monkeypatch.setattr(S.market_time, "now_et", lambda: NOW)
    monkeypatch.setattr(S.db, "set_readonly", lambda flag: calls.append(("set_readonly", flag)))
    monkeypatch.setattr(S.db, "load_trades_or_none", lambda: calls.append("trades") or frame(*j))
    monkeypatch.setattr(S.db, "load_snaptrade_fills_or_none",
                        lambda since: calls.append(("fills", since)) or f)
    monkeypatch.setattr(S.db, "load_snaptrade_txn_synced_at_or_none",
                        lambda: calls.append("synced") or NOW)
    assert S.main([]) == 0
    assert calls[0] == ("set_readonly", True)
    assert calls[1:] == ["trades", ("fills", jbm.fills_read_since(NOW).isoformat()), "synced"]
    out = capsys.readouterr().out
    assert "Journal rows: 3; Robinhood fills read since 2026-10-" in out
    assert 'Phase-2 gate count (severity "differs"): 4' in out


def test_main_refuses_when_a_read_fails(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "x")
    monkeypatch.setenv("SUPABASE_KEY", "y")
    monkeypatch.setattr(S.market_time, "now_et", lambda: NOW)
    monkeypatch.setattr(S.db, "set_readonly", lambda flag: None)
    monkeypatch.setattr(S.db, "load_trades_or_none", lambda: frame())
    monkeypatch.setattr(S.db, "load_snaptrade_fills_or_none", lambda since: None)
    with pytest.raises(SystemExit) as e:
        S.main([])
    assert e.value.code == 2 and "could not be read" in capsys.readouterr().err
    monkeypatch.setattr(S.db, "load_trades_or_none", lambda: None)
    with pytest.raises(SystemExit) as e:
        S.main([])
    assert e.value.code == 2


def test_script_has_no_write_call():
    src = Path(S.__file__).read_text(encoding="utf-8").replace("sys.path.insert(", "")
    for name in ("save_", "update_", "delete_", "upsert", "insert", ".write("):
        assert name not in src, name
