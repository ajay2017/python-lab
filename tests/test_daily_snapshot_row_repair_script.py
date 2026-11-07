"""scripts/daily_snapshot_row_repair.py (N1 C7): dry run writes nothing and
prints the plan, --apply writes per date then re-plans to nothing."""
import importlib.util
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

pytestmark = pytest.mark.fast

_SPEC = importlib.util.spec_from_file_location(
    "daily_snapshot_row_repair",
    Path(__file__).resolve().parent.parent / "scripts" / "daily_snapshot_row_repair.py")
S = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(S)

TODAY = date(2026, 11, 7)
BOOK_05 = {"CRWD": {"shares": 3.0, "close": 480.0}, "CSCO": {"shares": 15.0, "close": 75.0},
           "DASH": {"shares": 10.0, "close": 210.0}}
BOOK_28 = {"AAA": {"shares": 5.0, "close": 20.0}}


def _acct(d, book, repaired_at):
    return {"snapshot_date": d, "gross_book": 1.0, "book_positions": book,
            "repaired_at": repaired_at}


class World:
    """11-05 repaired (stale snapshots); 10-28 repaired (stale + a late exit);
    11-04 NOT repaired (differs, must be left alone); 11-07 (today) marked
    repaired (must never be touched)."""

    def __init__(self):
        self.acct = [
            _acct("2026-10-28", BOOK_28, "2026-11-01T12:00:00-04:00"),
            _acct("2026-11-04", BOOK_28, None),
            _acct("2026-11-05", BOOK_05, "2026-11-06T12:00:00-05:00"),
            _acct("2026-11-07", BOOK_05, "2026-11-07T12:00:00-05:00"),
        ]
        self.snap = [
            {"snapshot_date": "2026-10-28", "ticker": "AAA", "shares": 5, "close_price": 20.0},
            {"snapshot_date": "2026-10-28", "ticker": "GONE", "shares": 2, "close_price": 9.0},
            {"snapshot_date": "2026-11-04", "ticker": "AAA", "shares": 1, "close_price": 20.0},
            {"snapshot_date": "2026-11-05", "ticker": "CSCO", "shares": 13, "close_price": 75.0},
            {"snapshot_date": "2026-11-05", "ticker": "DASH", "shares": 7, "close_price": 210.0},
            {"snapshot_date": "2026-11-07", "ticker": "CSCO", "shares": 1, "close_price": 75.0},
        ]
        self.writes = []
        self.ok = True


@pytest.fixture
def world(monkeypatch):
    w = World()
    monkeypatch.setenv("SUPABASE_URL", "u")
    monkeypatch.setenv("SUPABASE_KEY", "k")
    monkeypatch.setattr(S, "today_et", lambda: TODAY)
    monkeypatch.setattr(S.db, "load_account_daily_snapshots",
                        lambda start_date=None: pd.DataFrame(w.acct))
    monkeypatch.setattr(S.db, "load_daily_snapshots_or_none",
                        lambda start_date=None: pd.DataFrame(w.snap))

    def _write(d, ups, dels):
        w.writes.append((d, ups, dels))
        if not w.ok:
            return None
        w.snap = [r for r in w.snap if not (r["snapshot_date"] == d and (
            r["ticker"] in dels or r["ticker"] in {u["ticker"] for u in ups}))]
        w.snap += [{"snapshot_date": d, **u} for u in ups]
        return True
    monkeypatch.setattr(S.db, "repair_daily_snapshot_rows", _write)
    return w


def test_dry_run_writes_nothing_and_prints_the_planned_rows(world, capsys):
    assert S.main([]) == 0
    out = capsys.readouterr().out
    assert world.writes == []
    assert "2026-11-05" in out and "CRWD" in out and "(no row) -> 3 sh" in out
    assert "CSCO" in out and "13 sh -> 15 sh" in out and "DASH" in out and "7 sh -> 10 sh" in out
    assert "GONE" in out and "[delete]" in out
    assert "book gross" in out and "per-ticker sum" in out
    assert "Dry run: nothing written" in out
    # the unrepaired 11-04 row and today's row are not in the plan at all
    assert "2026-11-04" not in out and "2026-11-07" not in out


def test_projected_effect_reconciles_the_per_ticker_sum_to_the_book(world, capsys):
    S.main([])
    out = capsys.readouterr().out
    book = 3 * 480.0 + 15 * 75.0 + 10 * 210.0
    assert f"book gross {book:,.2f}" in out
    assert f"per-ticker sum {13 * 75.0 + 7 * 210.0:,.2f} -> {book:,.2f}" in out


def test_apply_writes_each_date_then_replans_to_nothing(world, capsys):
    assert S.main(["--apply"]) == 0
    out = capsys.readouterr().out
    assert [w[0] for w in world.writes] == ["2026-10-28", "2026-11-05"]
    assert world.writes[0][2] == ["GONE"] and world.writes[0][1] == []
    assert {u["ticker"]: u["shares"] for u in world.writes[1][1]} == {"CRWD": 3, "CSCO": 15, "DASH": 10}
    assert "applied 2026-11-05" in out and "idempotent" in out
    # nothing was written for the unrepaired day or for today
    assert all(w[0] not in ("2026-11-04", "2026-11-07") for w in world.writes)
    world.writes.clear()
    assert S.main(["--apply"]) == 0 and world.writes == []


def test_apply_with_a_failed_write_exits_nonzero_and_names_the_date(world, capsys):
    world.ok = False
    assert S.main(["--apply"]) == 1
    assert "FAILED  2026-11-05" in capsys.readouterr().out


def test_unreadable_tables_exit_with_a_message_and_write_nothing(world, monkeypatch, capsys):
    monkeypatch.setattr(S.db, "load_daily_snapshots_or_none", lambda start_date=None: None)
    with pytest.raises(SystemExit) as e:
        S.main(["--apply"])
    assert e.value.code == 2 and world.writes == []
    assert "daily_snapshots could not be read" in capsys.readouterr().err
    monkeypatch.setattr(S.db, "load_account_daily_snapshots", lambda start_date=None: None)
    with pytest.raises(SystemExit) as e:
        S.main([])
    assert e.value.code == 2


def test_a_repaired_row_with_no_book_is_listed_as_refused_never_written(world, capsys):
    world.acct[2]["book_positions"] = None
    assert S.main(["--apply"]) == 4            # the plannable date is still written
    out = capsys.readouterr().out
    assert "[no_book]" in out
    assert [w[0] for w in world.writes] == ["2026-10-28"]


def test_a_refused_date_exits_4_in_a_dry_run_too_and_writes_nothing(world, capsys):
    world.acct.append(_acct("2026-10-31", BOOK_28, "2026-11-01T12:00:00-04:00"))   # a Saturday
    assert S.main([]) == 4
    cap = capsys.readouterr()
    assert "[non_trading_day]" in cap.out and "2026-10-31" in cap.out
    assert "Refused dates remain (1)" in cap.err and world.writes == []


def test_apply_with_a_refusal_writes_the_rest_exits_4_and_a_failed_write_wins(world, capsys):
    world.acct.append(_acct("2026-10-31", BOOK_28, "2026-11-01T12:00:00-04:00"))
    assert S.main(["--apply"]) == 4
    assert [w[0] for w in world.writes] == ["2026-10-28", "2026-11-05"]
    assert "idempotent" in capsys.readouterr().out
    world.snap = World().snap
    world.writes.clear()
    world.ok = False
    assert S.main(["--apply"]) == 1


def test_nothing_repaired_is_a_clean_noop(world, capsys):
    for r in world.acct:
        r["repaired_at"] = None
    assert S.main(["--apply"]) == 0
    assert world.writes == [] and "nothing to do" in capsys.readouterr().out


def test_missing_credentials_abort(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    with pytest.raises(SystemExit):
        S.main([])
