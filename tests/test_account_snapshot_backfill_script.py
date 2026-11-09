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
    assert "week ending 2026-11-01" in out and "Monthly returns (Reports)" in out
    assert "October 2026" in out and "Explained-gap" not in out


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


# -- --explain (N1-LogTime): owner-named trades that explain a "needs review" gap ----

class GapWorld:
    """Two legacy rows whose gross_book is short by one hand-logged BUY each:
    09-22 by ON #388 (10 @ 73.60 = 736.00), 09-29 by META #396 (1 @ 500.00)."""

    def __init__(self):
        self.acct = [_legacy("2026-09-22", 500.0, -300.0),
                     _legacy("2026-09-29", 1236.0, -300.0)]
        self.trades = pd.DataFrame([
            _trade(1, "AAA", 10, EARLY),
            _trade(388, "ON", 10, "2026-09-22T19:30:00+00:00"),
            _trade(396, "META", 1, "2026-09-29T19:30:00+00:00")])
        rows = []
        for d, sh in (("2026-09-22", {"AAA": 10, "ON": 10}),
                      ("2026-09-29", {"AAA": 10, "ON": 10, "META": 1})):
            for tk, n in sh.items():
                px = {"AAA": 50.0, "ON": 73.6, "META": 500.0}[tk]
                rows.append({"snapshot_date": d, "ticker": tk, "shares": n, "close_price": px})
        self.snap = pd.DataFrame(rows)
        self.applied = []
        self.fields = {}
        self.write_ok = True
        self.persist = True


@pytest.fixture
def gap(monkeypatch):
    w = GapWorld()
    monkeypatch.setenv("SUPABASE_URL", "u")
    monkeypatch.setenv("SUPABASE_KEY", "k")
    monkeypatch.setattr(S.db, "load_account_daily_snapshots",
                        lambda start_date=None: pd.DataFrame(w.acct))
    monkeypatch.setattr(S.db, "load_trades_or_none", lambda: w.trades)
    monkeypatch.setattr(S.db, "load_daily_snapshots_or_none", lambda start_date=None: w.snap)
    monkeypatch.setattr(S.db, "load_weekly_debriefs", lambda limit=4: pd.DataFrame())
    monkeypatch.setattr(S.db, "account_snapshot_provenance_available", lambda: True)

    def _apply(d, fields):
        w.applied.append(d)
        w.fields[d] = fields
        if not w.write_ok:
            return False
        if w.persist:
            for r in w.acct:
                if r["snapshot_date"] == d:
                    r.update(fields)
        return True
    monkeypatch.setattr(S.db, "apply_account_snapshot_repair", _apply)
    monkeypatch.setattr(S.data_mod, "fetch_unadjusted_history", lambda tk, period: None)
    return w


@pytest.mark.parametrize("bad", [
    ["2026-09-22"],                                   # no =IDS
    ["09-22=388"],                                    # bad date
    ["2026-09-22=abc"],                               # not an int
    ["2026-09-22=0"],                                 # not positive
    ["2026-09-22=388+"],                              # empty id
    ["2026-09-22=388", "2026-09-22=389"],             # date repeated
    ["2026-09-22=388", "2026-09-29=388"],             # id named for two dates
    ["2026-09-22=3²"],                            # superscript digit (isdigit but not int)
    ["2026-09-01=388"],                               # before --since (default 2026-09-10)
])
def test_explain_parse_errors_die_with_exit_2(gap, bad):
    argv = [x for spec in bad for x in ("--explain", spec)]
    with pytest.raises(SystemExit) as e:
        S.main(argv)
    assert e.value.code == 2 and gap.applied == []


def test_explain_dry_run_prints_the_section_and_writes_nothing(gap, capsys):
    rc = S.main(["--explain", "2026-09-22=388", "--explain", "2026-09-29=396"])
    out = capsys.readouterr().out
    assert rc == 0 and gap.applied == []
    assert "Explained-gap repairs (dates and trades named by you): 2 to repair, 0 refused." in out
    assert "trade 388  BUY 10 ON @ 73.6000  = +736.00" in out
    assert "trade 396  BUY 1 META @ 500.0000  = +500.00" in out
    assert "gap before (gross - journal book x closes): -736.00; residual after: 0.00" in out
    assert "gross     500.00 -> 1,236.00" in out
    assert "Monthly returns (Reports) that may read a repaired date: September 2026 " in out
    assert "(repaired: 2026-09-22, 2026-09-29)" in out
    assert "and the explained-gap repairs above" in out


def test_explain_apply_writes_the_explained_fields_and_exits_0(gap, capsys):
    rc = S.main(["--explain", "2026-09-22=388", "--explain", "2026-09-29=396", "--apply"])
    out = capsys.readouterr().out
    assert rc == 0
    assert gap.applied == ["2026-09-22", "2026-09-29"]
    assert "applied 2026-09-22 [explained_gap]" in out
    f = gap.fields["2026-09-22"]
    assert f["gross_book"] == 1236.0 and f["journal_max_trade_id"] == 396
    assert f["original_gross_book"] == 500.0 and "book_positions" in f
    assert "cash_balance" not in f and "cash_as_of" not in f
    assert gap.fields["2026-09-29"]["gross_book"] == 1736.0
    assert "no applied explained-gap date is a review or repair again" in out
    # a second run: both dates now have provenance, so they are refused (exit 4 on --apply)
    gap.applied.clear()
    assert S.main(["--explain", "2026-09-22=388"]) == 0
    assert "already has provenance" in capsys.readouterr().out
    assert S.main(["--explain", "2026-09-22=388", "--apply"]) == 4
    assert gap.applied == []


def test_refused_explained_date_exits_4_but_other_writes_are_made(gap, capsys):
    rc = S.main(["--explain", "2026-09-22=388", "--explain", "2026-09-29=999", "--apply"])
    cap = capsys.readouterr()
    assert rc == 4
    assert gap.applied == ["2026-09-22"]
    assert "2026-09-29: trade id 999 is not in the journal" in cap.out
    assert "refused" in cap.err


def test_explained_write_failure_exits_1(gap):
    gap.write_ok = False
    assert S.main(["--explain", "2026-09-22=388", "--explain", "2026-09-29=999", "--apply"]) == 1


def test_explained_date_still_a_review_after_apply_exits_3(gap, capsys):
    gap.persist = False                                # the write "succeeds" but nothing sticks
    rc = S.main(["--explain", "2026-09-22=388", "--apply"])
    assert rc == 3
    assert "still proposes explained dates" in capsys.readouterr().err


def test_no_explain_output_has_no_explained_section(gap, capsys):
    assert S.main([]) == 0
    out = capsys.readouterr().out
    assert "Explained-gap" not in out and "explained-gap repairs above" not in out
    assert "2 need review" in out
    assert S.main(["--json"]) == 0
    j = capsys.readouterr().out
    assert '"explained"' not in j and '"explained_refusals"' not in j


def test_explain_json_adds_the_explained_keys(gap, capsys):
    assert S.main(["--json", "--explain", "2026-09-22=388", "--explain", "2026-09-29=999"]) == 0
    j = capsys.readouterr().out
    assert '"explained"' in j and '"explained_refusals"' in j
    assert '"explained_gap"' in j and "not in the journal" in j


def test_monthly_hint_months():
    h = S.monthly_hint({date(2026, 9, 30)})
    assert "September 2026, October 2026" in h and "(repaired: 2026-09-30)" in h
    h = S.monthly_hint({date(2026, 10, 19)})
    assert "October 2026 (repaired: 2026-10-19)" in h and "November" not in h
    h = S.monthly_hint({date(2026, 9, 30), date(2026, 10, 19)})
    assert "September 2026, October 2026 (repaired: 2026-09-30, 2026-10-19)" in h
    assert "Custom period, or Last month when it is the month before this one" in h
    assert "Nothing to regenerate: after applying, hard-refresh" in h
    assert S.monthly_hint(set()) is None
    # year roll: the last trading day of December is also January's start anchor
    assert "December 2026, January 2027" in S.monthly_hint({date(2026, 12, 31)})
    # a day before a weekend month-end is not itself the month's last trading day
    assert "November 2026" not in S.monthly_hint({date(2026, 10, 29)})
