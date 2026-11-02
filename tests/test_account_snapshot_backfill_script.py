"""scripts/account_snapshot_backfill.py (N1 C3): dry run writes nothing,
--apply gates on the provenance probe, partial repairs need --apply-partial."""
import importlib.util
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from stock_analyzer.market_time import ET

pytestmark = pytest.mark.fast

_SPEC = importlib.util.spec_from_file_location(
    "account_snapshot_backfill",
    Path(__file__).resolve().parent.parent / "scripts" / "account_snapshot_backfill.py")
S = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(S)

ANCHOR_30 = "2026-10-30T20:00:00+00:00"
ANCHOR_29 = "2026-10-29T20:00:00+00:00"
EARLY = "2026-09-10T20:00:00+00:00"


def _legacy(d, gross, cash):
    return {"snapshot_date": d, "gross_book": gross, "cash_balance": cash,
            "net_equity": gross + cash, "leverage": gross / (gross + cash),
            "maintenance_rate": 0.25, "cash_as_of": f"{d}T21:02:00+00:00",
            "journal_max_trade_id": None, "book_positions": None,
            "original_gross_book": None, "repaired_at": None}


def _trade(i, tk, n, ts):
    return {"id": i, "ticker": tk, "action": "BUY", "shares": n, "price": 1.0, "traded_at": ts}


class World:
    """Fake DB state. 10-29: MU is new (missing_ticker). 10-30: CSCO is new and
    DASH 5 -> 7 (partial_quantity). CSCO's close comes from the Yahoo fallback."""

    def __init__(self):
        self.acct = [_legacy("2026-10-29", 500.0, -300.0),
                     _legacy("2026-10-30", 700.0, -500.0)]
        self.trades = pd.DataFrame([
            _trade(1, "DASH", 5, EARLY), _trade(2, "MU", 2, ANCHOR_29),
            _trade(3, "CSCO", 5, ANCHOR_30), _trade(4, "DASH", 2, ANCHOR_30)])
        rows = []
        for d, sh in (("2026-10-29", {"DASH": 5, "MU": 0}),
                      ("2026-10-30", {"DASH": 5, "MU": 2})):
            for tk, n in sh.items():
                rows.append({"snapshot_date": d, "ticker": tk, "shares": n, "close_price": 100.0})
        self.snap = pd.DataFrame(rows)
        self.applied = []
        self.avail = True


@pytest.fixture
def world(monkeypatch):
    w = World()
    monkeypatch.setenv("SUPABASE_URL", "u")
    monkeypatch.setenv("SUPABASE_KEY", "k")
    monkeypatch.setattr(S.db, "load_account_daily_snapshots",
                        lambda start_date=None: pd.DataFrame(w.acct))
    monkeypatch.setattr(S.db, "load_trades_or_none", lambda: w.trades)
    monkeypatch.setattr(S.db, "load_daily_snapshots_or_none", lambda start_date=None: w.snap)
    monkeypatch.setattr(S.db, "load_weekly_debriefs", lambda limit=4: pd.DataFrame(
        [{"week_ending": "2026-11-01"}, {"week_ending": "2026-10-04"}]))
    monkeypatch.setattr(S.db, "account_snapshot_provenance_available", lambda: w.avail)

    def _apply(d, fields):
        w.applied.append(d)
        for r in w.acct:
            if r["snapshot_date"] == d:
                r.update(fields)
        return True
    monkeypatch.setattr(S.db, "apply_account_snapshot_repair", _apply)
    monkeypatch.setattr(
        S.data_mod, "fetch_unadjusted_history",
        lambda tk, period: pd.DataFrame(
            {"Close": [50.0]}, index=pd.to_datetime(["2026-10-30"])) if tk == "CSCO" else None)
    return w


def test_dry_run_makes_zero_writes_and_reports(world, capsys):
    rc = S.main([])
    out = capsys.readouterr().out
    assert rc == 0 and world.applied == []
    assert "partial_quantity" in out and "missing_ticker" in out
    assert "Dry run: nothing written" in out
    assert "week ending 2026-11-01" in out and "October monthly return" in out


def test_apply_aborts_when_probe_false_or_none(world):
    for v in (False, None):
        world.avail = v
        with pytest.raises(SystemExit):
            S.main(["--apply"])
        assert world.applied == []


def test_apply_missing_ticker_only_and_partial_needs_the_flag(world, capsys):
    rc = S.main(["--apply"])
    out = capsys.readouterr().out
    assert rc == 0
    assert world.applied == ["2026-10-29"]
    assert "Not applied (owner review" in out and "2026-10-30" in out
    assert "idempotent" in out
    world.applied.clear()
    assert S.main(["--apply", "--apply-partial", "2026-10-30"]) == 0
    assert world.applied == ["2026-10-30"]


def test_json_output_and_bad_args(world, capsys):
    assert S.main(["--json"]) == 0
    assert '"repairs"' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        S.main(["--apply-partial", "10-30"])
    with pytest.raises(SystemExit):
        S.main(["--since", "yesterday"])


def test_missing_env_and_unreadable_trades_abort(world, monkeypatch):
    monkeypatch.setattr(S.db, "load_trades_or_none", lambda: None)
    with pytest.raises(SystemExit):
        S.main([])
    monkeypatch.delenv("SUPABASE_KEY")
    with pytest.raises(SystemExit):
        S.main([])


def test_affected_debriefs_window():
    df = pd.DataFrame([{"week_ending": "2026-11-01"}, {"week_ending": "2026-11-20"}])
    # week ending Sun 11-01: end anchor 10-30, start anchor 10-23
    assert S.affected_debriefs({date(2026, 10, 30)}, df) == [
        (date(2026, 11, 1), [date(2026, 10, 30)])]
    assert S.affected_debriefs({date(2026, 10, 23)}, df) == [
        (date(2026, 11, 1), [date(2026, 10, 23)])]
    assert S.affected_debriefs({date(2026, 10, 27)}, df) == []


def test_unreconciled_repair_is_listed_but_needs_apply_partial(world, capsys):
    world.acct[0]["gross_book"] = 520.0          # unrelated drift on 10-29
    assert S.main([]) == 0
    out = capsys.readouterr().out
    assert "[unreconciled]" in out and "NOT written" in out
    assert S.main(["--apply"]) == 0
    assert world.applied == []                   # neither 10-29 (unreconciled) nor 10-30 (partial)
    assert S.main(["--apply", "--apply-partial", "2026-10-29"]) == 0
    assert world.applied == ["2026-10-29"]


def test_needs_review_row_is_listed_and_never_applied(world, capsys):
    world.snap = pd.concat([world.snap, pd.DataFrame(
        [{"snapshot_date": "2026-10-29", "ticker": "MU", "shares": 2, "close_price": 100.0}])]
    ).drop_duplicates(["snapshot_date", "ticker"], keep="last")
    assert S.main(["--apply"]) == 0
    out = capsys.readouterr().out
    assert "Needs review" in out and "2026-10-29" in out
    assert "2026-10-29" not in world.applied


def test_empty_debrief_frame_prints_the_unreadable_note(world, monkeypatch, capsys):
    monkeypatch.setattr(S.db, "load_weekly_debriefs", lambda limit=4: pd.DataFrame())
    S.main([])
    assert "debriefs couldn't be read, or none exist" in capsys.readouterr().out
