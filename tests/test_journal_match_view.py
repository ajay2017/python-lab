"""TJDate U8a: `stock_analyzer.journal_match_view`, the Home line and the 💰 Account list of
the journal-vs-Robinhood comparison (docs/plans/journal-vs-robinhood-u8.md).

The count / tier / dedupe rules are tested on hand-built findings (every key a real
finding carries); the end-to-end and time-context tests run the real matcher on the
fixtures `test_journal_broker_match` already pins. Time-context matrix: a Saturday and a
Thanksgiving visit, morning vs evening on one ET date, the ET midnight, the DST change in
the sync wording, a six-day-old sync, a trade logged a day late and a back-dated one (BUY
and SELL), a naive `now_et`.
"""
import ast
import random
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from stock_analyzer import constants as C
from stock_analyzer import journal_broker_match as jbm
from stock_analyzer import journal_match_view as jmv
from stock_analyzer import pending_import_verdict as pv
from stock_analyzer import trade_entry_checks as tec
from tests.test_journal_broker_match import (
    NOW, SYNC, _444_rows, _csco, _fuzz_case, _mrvl_419, et, fl, frame, jrow, run)

pytestmark = pytest.mark.fast

K = jbm
HOME_PLAIN_WARNING_TAIL = " → 💰 Account → Broker Sync. Holdings, weights and sizes are computed from your journal."


def ff(kind, tk="X", act="BUY", holdings=None, jp=None, bp=None, statuses=("dismissed",),
       txns=("T1",), pids=(1,), jids=(), jdates=(), fdates=(), js=None, bs=None, text=None,
       differs_on=(), **over):
    """A finding with every key the matcher's `_finding` writes. `differs_on="absent"`
    leaves the key out (an old finding)."""
    f = {"kind": kind, "severity": jbm._SEVERITY[kind], "rank": 10, "ticker": tk, "action": act,
         "journal_ids": list(jids), "journal_dates": list(jdates), "txn_ids": list(txns),
         "pending_ids": list(pids), "fill_dates": list(fdates), "placed_dates": list(fdates),
         "fill_statuses": list(statuses), "journal_shares": js, "journal_price": jp,
         "broker_shares": bs, "broker_price": bp, "holdings_effect": holdings,
         "correction_shape": None, "one_click_eligible": False, "one_click_block": None,
         "requires_fill_date_check": False, "nearby_journal_ids": [], "possible_duplicate_of": None,
         "differs_on": list(differs_on) if differs_on != "absent" else None,
         "text": text if text is not None else f"Matcher text for {kind}."}
    if differs_on == "absent":
        del f["differs_on"]
    f.update(over)
    return f


def linked_f(on, tk="X", act="BUY", holdings=0.0, jp=100.0, bp=100.0, **kw):
    """A LINKED_DIFFERS finding differing on the tokens in `on`."""
    return ff(K.KIND_LINKED_DIFFERS, tk=tk, act=act, holdings=holdings, jp=jp, bp=bp,
              differs_on=on, **kw)


def shares_f(tk="TSLA", act="SELL", kind=K.KIND_UNLOGGED, holdings=5.0, **kw):
    return ff(kind, tk=tk, act=act, holdings=holdings, **kw)


def price_f(tk="X", **kw):
    return ff(K.KIND_PRICE_FIX, tk=tk, holdings=0.0, jp=100.0, bp=101.0, **kw)


def drift(state="drift", **diff):
    base = {"rh_only": [], "app_only": [], "qty_mismatch": []}
    base.update(diff)
    return {"state": state, "diff": base}


def home(findings, drift_verdict=None, txn=SYNC, full=SYNC, linked=True):
    return jmv.home_line(findings, drift_verdict, txn, full, linked)


def acct(findings, txn=SYNC, now=NOW, pending_visible=False, parts=()):
    return jmv.account_list(findings, txn, now, pending_visible, parts)


def items(a):
    return [i for g in a["groups"] for i in g["items"]]


def strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from strings(v)


def assert_plain(obj):
    for s in strings(obj):
        assert "$" not in s and "**" not in s, s
        assert not any(line.lstrip().startswith("#") for line in s.splitlines()), s


# ── the count rule (UE-1) ────────────────────────────────────────────────────

def test_every_matcher_kind_is_in_exactly_one_account_group():
    seen = {}
    for key, _title, kinds in jmv.ACCOUNT_GROUPS:
        for k in kinds:
            assert k not in seen, f"{k} is in groups {seen[k]} and {key}"
            seen[k] = key
    assert set(seen) == set(jbm._SEVERITY), set(jbm._SEVERITY) ^ set(seen)   # a new matcher kind fails here
    assert set(jmv._GROUP_OF_KIND) == set(jbm._SEVERITY)


def test_group_order_and_titles_are_pinned():
    assert [(k, t) for k, t, _ in jmv.ACCOUNT_GROUPS] == [
        ("differs", "Differs"), ("dates", "Dates differ; the journal may be right"),
        ("unmatched", "Couldn't match automatically"), ("not_compared", "Not compared yet"),
        ("matches", "Matches, nothing to fix")]
    groups = {k: kinds for k, _t, kinds in jmv.ACCOUNT_GROUPS}
    assert groups["dates"] == {K.KIND_REDATE, K.KIND_SPLIT}
    assert groups["unmatched"] == {K.KIND_AMBIGUOUS, K.KIND_TOO_MANY}
    assert groups["not_compared"] == {K.KIND_NOT_YET_COMPARED, K.KIND_UNCHECKED}
    assert groups["matches"] == {K.KIND_LINK, K.KIND_RECONCILED, K.KIND_SKIPPED_NON_EQUITY}


def test_home_counted_kinds_are_severity_differs_minus_redate_and_split():
    differs = {k for k, s in jbm._SEVERITY.items() if s == jbm.SEV_DIFFERS}
    assert jmv.HOME_COUNTED_KINDS == differs - {K.KIND_REDATE, K.KIND_SPLIT}
    assert jmv.HOME_COUNTED_KINDS == {
        K.KIND_UNLOGGED, K.KIND_JOURNAL_ONLY, K.KIND_QTY_MISMATCH, K.KIND_PRICE_FIX,
        K.KIND_FORWARD, K.KIND_MERGED_FILLS, K.KIND_LINKED_DIFFERS}
    assert isinstance(jmv.HOME_COUNTED_KINDS, frozenset)


@pytest.mark.parametrize("kind", sorted(jbm._SEVERITY))
def test_only_the_counted_kinds_are_counted(kind):
    f = ff(kind, holdings=0.0, jp=100.0, bp=101.0)
    line = home([f])
    if kind in jmv.HOME_COUNTED_KINDS:
        assert line["counted"] == 1 and line["tier"] == "caption", kind
    else:
        assert line is None or line["counted"] == 0, kind        # REDATE / SPLIT / unresolved / info


def test_redate_and_split_are_never_counted_but_forward_merged_and_price_fix_are():
    assert home([ff(K.KIND_REDATE, holdings=0.0), ff(K.KIND_SPLIT, holdings=0.0)]) is None
    line = home([ff(K.KIND_FORWARD, holdings=0.0), ff(K.KIND_MERGED_FILLS, holdings=0.0), price_f()])
    assert line["counted"] == 3 and line["tier"] == "caption"


def test_unresolved_unchecked_and_info_kinds_never_make_a_home_line():
    fs = [ff(k, holdings=None) for k, s in jbm._SEVERITY.items() if s != jbm.SEV_DIFFERS]
    assert len(fs) == 7
    assert home(fs) is None


@pytest.mark.parametrize("kind", sorted(jmv.HOME_COUNTED_KINDS))
def test_every_counted_kind_lands_in_exactly_one_price_or_date_bucket(kind):
    for jp, bp in ((None, None), (100.0, None), (100.0, 100.0), (100.0, 100.005), (100.0, 101.0)):
        f = ff(kind, holdings=0.0, jp=jp, bp=bp)
        assert jmv.diff_bucket(f) in ("price", "date")
        line = home([f])
        assert line["counted"] == 1 and line["tier"] == "caption"
        want = "1 price, 0 date" if jmv.diff_bucket(f) == "price" else "0 price, 1 date"
        assert f"({want})" in line["text"], (kind, jp, bp, line["text"])


def test_the_bucket_table():
    assert jmv.diff_bucket(ff(K.KIND_PRICE_FIX, jp=100.0, bp=100.0)) == "price"      # whatever the prices say
    assert jmv.diff_bucket(ff(K.KIND_FORWARD, jp=100.0, bp=150.0)) == "date"
    assert jmv.diff_bucket(ff(K.KIND_MERGED_FILLS, jp=260.0, bp=250.0)) == "date"
    # LINKED_DIFFERS reads differs_on, whatever the prices say
    assert jmv.diff_bucket(linked_f(["price"], jp=100.0, bp=100.0)) == "price"
    assert jmv.diff_bucket(linked_f(["date"], jp=100.0, bp=150.0)) == "date"
    assert jmv.diff_bucket(linked_f(["price", "date"])) == "price"
    assert jmv.diff_bucket(linked_f([])) == "date"
    # the _same_price fallback only when differs_on is absent (or not a list)
    gone = lambda jp, bp: ff(K.KIND_LINKED_DIFFERS, jp=jp, bp=bp, differs_on="absent")
    assert jmv.diff_bucket(gone(100.0, 101.0)) == "price"
    assert jmv.diff_bucket(gone(100.0, 100.01)) == "date"          # the matcher's own tolerance
    assert jmv.diff_bucket(gone(None, 101.0)) == "date"
    assert jmv.diff_bucket(gone(100.0, None)) == "date"
    bad = ff(K.KIND_LINKED_DIFFERS, jp=100.0, bp=101.0)
    bad["differs_on"] = "price"                                    # not a list: the fallback
    assert jmv.diff_bucket(bad) == "price"


def test_the_2026_11_12_mix_counts_four_as_a_caption_with_three_price_one_date():
    mix = [price_f("SPCX"), price_f("TSLA"),
           linked_f(["price"], tk="ORCL", jp=100.0, bp=101.0),
           ff(K.KIND_FORWARD, tk="MRVL", holdings=0.0, jp=260.0, bp=250.0),
           ff(K.KIND_REDATE, holdings=0.0), ff(K.KIND_REDATE, holdings=0.0),
           ff(K.KIND_SPLIT, holdings=0.0), ff(K.KIND_AMBIGUOUS), ff(K.KIND_NOT_YET_COMPARED),
           ff(K.KIND_NOT_YET_COMPARED), ff(K.KIND_LINK), ff(K.KIND_LINK), ff(K.KIND_RECONCILED)]
    assert len(mix) == 13
    line = home(mix)
    assert line["tier"] == "caption" and line["counted"] == 4 and line["deferred"] == 0
    # the mix holds AMBIGUOUS and NOT_YET_COMPARED, so agreement isn't known: no clause
    assert line["text"] == ("ℹ️ Journal vs Robinhood: 4 differences in price or date (3 price, 1 date) "
                            "→ 💰 Account → Broker Sync.")
    assert "share counts agree" not in line["text"]


AGREE = "; share counts agree"


def test_the_share_counts_agree_clause_is_kept_only_where_agreement_is_known():
    base = [price_f("A"), ff(K.KIND_FORWARD, tk="B", holdings=0.0), ff(K.KIND_LINK), ff(K.KIND_REDATE, holdings=0.0)]
    clean = home(base)
    assert clean["text"] == ("ℹ️ Journal vs Robinhood: 2 differences in price or date (1 price, 1 date); "
                             "share counts agree → 💰 Account → Broker Sync.")
    # 1. an unresolved or unchecked finding anywhere in the list drops it
    for kind in (K.KIND_AMBIGUOUS, K.KIND_TOO_MANY, K.KIND_UNCHECKED, K.KIND_NOT_YET_COMPARED):
        assert AGREE not in home(base + [ff(kind)])["text"], kind
    odd = ff(K.KIND_LINK)
    odd["severity"] = "something new"                                    # an unknown severity fails closed
    assert AGREE not in home(base + [odd])["text"]
    del odd["severity"]
    assert AGREE not in home(base + [odd])["text"]
    # 2. unknown coverage drops it
    assert AGREE not in home(base, txn=None)["text"]
    assert AGREE not in home(base, txn="garbage")["text"]
    # 3. a UE-8 lag drops it
    assert AGREE not in home(base, txn=et(2026, 11, 11, 9, 0), full=et(2026, 11, 12, 9, 0))["text"]
    # 4. a deferral drops it
    assert AGREE not in home(base + [shares_f("TSLA")], drift(qty_mismatch=[{"ticker": "TSLA"}]))["text"]
    # and a clean case with a known sync, equal stamps and only info / differs kinds keeps it
    assert AGREE in home(base, txn=SYNC, full=SYNC)["text"]
    assert AGREE in home(base, txn=SYNC, full=None)["text"]


# ── the tier (UE-2) ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("f", [
    shares_f(),
    shares_f(tk="X", act="SELL", kind=K.KIND_QTY_MISMATCH, holdings=1.0),
    shares_f(tk="X", act="BUY", kind=K.KIND_JOURNAL_ONLY, holdings=5.0),
    shares_f(tk="X", act="BUY", kind=K.KIND_LINKED_DIFFERS, holdings=1.0, differs_on=["shares"]),
    linked_f(["ticker"]), linked_f(["action"]), linked_f(["date", "action"]),
    ff(K.KIND_LINKED_DIFFERS, holdings=0.0, differs_on="absent"),           # fail closed
])
def test_a_share_changing_counted_finding_is_a_warning(f):
    line = home([f])
    assert line["tier"] == "warning" and line["counted"] == 1
    assert line["text"].startswith("⚠️ Journal vs Robinhood: 1 difference that changes share counts (")
    assert line["text"].endswith(HOME_PLAIN_WARNING_TAIL)


def test_price_fix_only_is_a_caption_and_so_is_an_effect_inside_the_tolerance():
    assert home([price_f()])["tier"] == "caption"
    inside = C.BROKER_DRIFT_SHARE_TOL / 2
    assert not jmv.changes_holdings(shares_f(kind=K.KIND_QTY_MISMATCH, holdings=inside))
    assert home([shares_f(kind=K.KIND_QTY_MISMATCH, holdings=inside, jp=1.0, bp=2.0)])["tier"] == "caption"
    assert jmv.changes_holdings(shares_f(kind=K.KIND_QTY_MISMATCH, holdings=C.BROKER_DRIFT_SHARE_TOL * 2))
    assert jmv.changes_holdings(shares_f(kind=K.KIND_QTY_MISMATCH, holdings=-1.0))
    assert not jmv.changes_holdings(ff(K.KIND_PRICE_FIX, holdings=None))
    assert not jmv.changes_holdings(ff(K.KIND_PRICE_FIX, holdings=float("nan")))


def test_the_warning_sentence_is_exact_and_names_the_most_urgent_share_finding():
    line = home([shares_f(), price_f(), price_f("Y")])
    assert line["text"] == (
        "⚠️ Journal vs Robinhood: 1 difference that changes share counts (1 Robinhood sell of TSLA "
        "not in your journal); plus 2 in price or date" + HOME_PLAIN_WARNING_TAIL)
    assert line["counted"] == 3
    two = home([shares_f(), shares_f(tk="ORCL", act="BUY")])
    assert two["text"].startswith("⚠️ Journal vs Robinhood: 2 differences that change share counts "
                                  "(e.g. 1 Robinhood sell of TSLA not in your journal) →")


@pytest.mark.parametrize("f,clause", [
    (shares_f(tk="TSLA", act="SELL"), "1 Robinhood sell of TSLA not in your journal"),
    (shares_f(tk="TSLA", act="BUY"), "1 Robinhood buy of TSLA not in your journal"),
    (shares_f(tk="X", act="BUY", kind=K.KIND_JOURNAL_ONLY), "a journal buy of X with no Robinhood fill"),
    (shares_f(tk="X", act="SELL", kind=K.KIND_JOURNAL_ONLY), "a journal sell of X with no Robinhood fill"),
    (shares_f(tk="X", act="SELL", kind=K.KIND_QTY_MISMATCH),
     "X sell shares differ between your journal and Robinhood"),
    (shares_f(tk="X", act="BUY", kind=K.KIND_LINKED_DIFFERS, differs_on=["shares", "price"]),
     "a linked X buy differs in shares"),
    (linked_f(["ticker"], tk="TSLA", act="BUY"),
     "a linked TSLA buy differs in ticker or action from Robinhood's record"),
    (linked_f(["action"], tk="TSLA", act="SELL"),
     "a linked TSLA sell differs in ticker or action from Robinhood's record"),
    (linked_f(["shares", "action"], tk="X", act="BUY"),
     "a linked X buy differs in ticker or action from Robinhood's record"),
])
def test_the_example_clause_per_kind(f, clause):
    assert f"({clause})" in home([f])["text"]


# ── the broker-drift dedupe ──────────────────────────────────────────────────

def test_a_share_finding_on_a_drift_ticker_is_deferred_and_named():
    line = home([shares_f("TSLA")], drift(qty_mismatch=[{"ticker": "TSLA", "shares": 1}]))
    assert line["counted"] == 0 and line["deferred"] == 1 and line["deferred_tickers"] == ["TSLA"]
    assert line["tier"] == "caption"
    assert line["text"] == ("ℹ️ Journal vs Robinhood: TSLA: shown in the broker-drift notice above; "
                            "it is listed on 💰 Account.")


@pytest.mark.parametrize("where", ["rh_only", "app_only", "qty_mismatch"])
def test_each_drift_dimension_defers(where):
    line = home([shares_f("TSLA")], drift(**{where: [{"ticker": "tsla", "shares": 1}]}))
    assert line["deferred_tickers"] == ["TSLA"] and line["counted"] == 0


@pytest.mark.parametrize("verdict", [
    drift("awaiting_sync", qty_mismatch=[{"ticker": "TSLA"}]),
    drift("stale_clean", qty_mismatch=[{"ticker": "TSLA"}]),
    drift("unknown", qty_mismatch=[{"ticker": "TSLA"}]),
    drift("none", qty_mismatch=[{"ticker": "TSLA"}]),
    {"state": "none", "reason": "no_holdings"}, {"state": "drift"}, {"state": "drift", "diff": None},
    {"state": "drift", "diff": {"rh_only": None, "qty_mismatch": ["TSLA", 3]}}, None, "unknown", 7])
def test_only_a_drift_state_with_the_ticker_defers(verdict):
    line = home([shares_f("TSLA")], verdict)
    assert line["counted"] == 1 and line["deferred"] == 0 and line["tier"] == "warning"


def test_price_and_date_findings_are_never_deferred():
    verdict = drift(qty_mismatch=[{"ticker": "X"}])
    line = home([price_f("X"), ff(K.KIND_FORWARD, tk="X", holdings=0.0)], verdict)
    assert line["counted"] == 2 and line["deferred"] == 0 and line["tier"] == "caption"


def test_deferral_is_per_ticker_and_a_mixed_book_keeps_the_rest():
    verdict = drift(qty_mismatch=[{"ticker": "TSLA"}])
    line = home([shares_f("TSLA"), shares_f("ORCL", act="BUY"), price_f("TSLA")], verdict)
    assert line["tier"] == "warning" and line["counted"] == 2 and line["deferred"] == 1
    assert "(1 Robinhood buy of ORCL not in your journal); plus 1 in price or date" in line["text"]
    assert line["text"].endswith("TSLA: shown in the broker-drift notice above; it is listed on "
                                 "💰 Account.")
    two = home([shares_f("TSLA"), shares_f("AAPL")], drift(rh_only=[{"ticker": "TSLA"}, {"ticker": "AAPL"}]))
    assert two["deferred_tickers"] == ["AAPL", "TSLA"]
    assert "they are listed on 💰 Account." in two["text"]


def test_a_caption_with_a_deferred_ticker_does_not_claim_share_counts_agree():
    line = home([price_f("X"), shares_f("TSLA")], drift(qty_mismatch=[{"ticker": "TSLA"}]))
    assert line["tier"] == "caption" and "share counts agree" not in line["text"]
    assert "TSLA: shown in the broker-drift notice above" in line["text"]


# ── linked rows on the wrong ticker or action (reviewer probes) ──────────────

def test_the_reviewer_probes_real_matcher_a_wrong_action_or_ticker_with_equal_shares_is_a_warning():
    j = [jrow(1, "TSLA", "BUY", 5, 400.0, et(2026, 11, 10, 10), txn="T1")]
    for f in (fl(1, "T1", "TSLA", "SELL", 5, 400.0, date(2026, 11, 10), status="logged"),     # action
              fl(1, "T1", "TSLL", "BUY", 5, 400.0, date(2026, 11, 10), status="logged")):      # ticker
        res = run(j, [f])
        assert [r["kind"] for r in res] == ["LINKED_DIFFERS"] and res[0]["holdings_effect"] == 0.0
        line = home(res)
        assert line["tier"] == "warning" and line["counted"] == 1 and line["deferred"] == 0
        assert "differs in ticker or action from Robinhood's record" in line["text"]
        assert "share counts agree" not in line["text"] and "0 price, 1 date" not in line["text"]


def test_a_wrong_ticker_or_action_is_never_deferred_to_the_drift_notice():
    verdict = drift(qty_mismatch=[{"ticker": "TSLA"}], rh_only=[{"ticker": "TSLL"}])
    for f in (linked_f(["ticker"], tk="TSLA"), linked_f(["action"], tk="TSLA"),
              ff(K.KIND_LINKED_DIFFERS, tk="TSLA", holdings=0.0, differs_on="absent")):
        line = home([f], verdict)
        assert line["tier"] == "warning" and line["counted"] == 1 and line["deferred"] == 0
        assert line["deferred_tickers"] == []
    # a shares-only LINKED_DIFFERS on a drift ticker is still the drift notice's
    shares = linked_f(["shares"], tk="TSLA", holdings=1.0)
    line = home([shares], verdict)
    assert line["counted"] == 0 and line["deferred"] == 1 and line["deferred_tickers"] == ["TSLA"]
    # price- or date-only LINKED_DIFFERS never defers and is a caption
    for on, bucket in ((["price"], "1 price, 0 date"), (["date"], "0 price, 1 date")):
        line = home([linked_f(on, tk="TSLA")], verdict)
        assert line["tier"] == "caption" and line["counted"] == 1 and f"({bucket})" in line["text"]


def test_changes_holdings_for_linked_differs_reads_differs_on_and_fails_closed():
    for on, want in ((["ticker"], True), (["action"], True), (["shares"], True), (["price"], False),
                     (["date"], False), (["price", "date"], False), ([], False),
                     (["price", "action"], True)):
        assert jmv.changes_holdings(linked_f(on)) is want, on
    assert jmv.changes_holdings(ff(K.KIND_LINKED_DIFFERS, holdings=0.0, differs_on="absent")) is True
    bad = linked_f([])
    bad["differs_on"] = "ticker"
    assert jmv.changes_holdings(bad) is True                       # not a list
    # only LINKED_DIFFERS reads differs_on
    assert jmv.changes_holdings(ff(K.KIND_PRICE_FIX, holdings=0.0, differs_on=["ticker"])) is False


# ── the three states ─────────────────────────────────────────────────────────

def test_home_three_states_and_not_linked():
    unchecked = home(None)
    assert unchecked["tier"] == "caption" and unchecked["text"] == jmv.HOME_UNCHECKED_TEXT
    assert "not checked" in unchecked["text"] and unchecked["counted"] == 0
    assert home([]) is None                                    # clean: Home never prints an all-clear
    assert home([shares_f()])["tier"] == "warning"
    for fs in (None, [], [shares_f()]):
        assert home(fs, linked=False) is None
        assert home(fs, linked=None) is None


def test_home_garbage_findings_read_as_not_checked_and_non_dicts_are_dropped():
    assert home("x")["text"] == jmv.HOME_UNCHECKED_TEXT
    assert home([1, None, "x"]) is None


def test_home_nothing_counted_unknown_coverage_is_a_caption_with_the_coverage_text():
    line = home([], txn=None, full=SYNC)
    assert line["tier"] == "caption"
    assert line["text"] == "ℹ️ Journal vs Robinhood: " + jbm.match_coverage(None)["text"]
    assert home([ff(K.KIND_LINK)], txn="not a time", full=SYNC)["tier"] == "caption"


def test_home_nothing_counted_a_known_clean_sync_is_silent():
    assert home([], txn=SYNC, full=SYNC) is None
    assert home([ff(K.KIND_LINK)], txn=SYNC, full=SYNC) is None


def test_ue8_home_says_when_the_transaction_import_didnt_finish():
    txn, full = et(2026, 11, 11, 9, 0), et(2026, 11, 12, 9, 0)
    line = home([], txn=txn, full=full)
    assert line["tier"] == "caption"
    assert line["text"] == ("ℹ️ Journal vs Robinhood: The last Robinhood sync run didn't finish "
                            "importing transactions, so fills after Wed Nov 11, 9:00 AM ET may not "
                            "be imported yet, so they may not be compared with your journal.")
    assert home([], txn=full, full=full) is None                # equal stamps: no sentence
    assert home([], txn=full, full=txn) is None                 # txn newer: no sentence


# ── Account: the three states, groups, items ─────────────────────────────────

def test_account_offline_names_the_failed_part():
    for parts, words in ((["journal"], "your journal couldn't"),
                         (["fills"], "Robinhood's fills couldn't"),
                         (["journal", "fills"], "your journal and Robinhood's fills couldn't"),
                         (("fills", "journal"), "your journal and Robinhood's fills couldn't"),
                         ("journal", "your journal couldn't")):
        a = acct(None, parts=parts)
        assert a["state"] == "offline" and words in a["offline_text"], (parts, a["offline_text"])
        assert a["groups"] == [] and a["in_pending_count"] == 0
    for parts in ((), [], None, ["nonsense"]):
        a = acct(None, parts=parts)
        assert a["state"] == "offline" and "the comparison didn't run this render" in a["offline_text"]
    assert acct("x", parts=["journal"])["state"] == "offline"
    assert acct([], parts=["journal"])["state"] == "clean"       # parts only matter when findings is None


def test_account_clean_carries_the_coverage_and_never_says_matches_robinhood():
    a = acct([])
    assert a["state"] == "clean" and a["groups"] == [] and a["offline_text"] == ""
    assert a["summary_text"] == "No differences found."
    assert a["coverage_text"] == jbm.match_coverage(SYNC)["text"]
    assert a["window_text"] == "Compared: journal rows and Robinhood fills dated Thu Oct 8 or later."
    unknown = acct([], txn=None)
    assert unknown["coverage_text"] == jbm.match_coverage(None)["text"]
    for a in (a, unknown):
        for s in strings(a):
            assert "matches Robinhood" not in s and "match Robinhood" not in s, s


def test_account_unreadable_txn_stamp_is_not_offline():
    a = acct([shares_f()], txn="garbage")
    assert a["state"] == "rows" and a["coverage_text"] == jbm.match_coverage(None)["text"]


def test_account_groups_in_order_with_empty_groups_left_out_and_matcher_order_kept():
    fs = [ff(K.KIND_LINK, tk="L1"), shares_f("T1"), ff(K.KIND_NOT_YET_COMPARED, tk="N1"),
          price_f("P1"), ff(K.KIND_REDATE, tk="R1", holdings=0.0), ff(K.KIND_AMBIGUOUS, tk="A1"),
          ff(K.KIND_SPLIT, tk="S1", holdings=0.0), ff(K.KIND_UNCHECKED, tk="U1"),
          ff(K.KIND_TOO_MANY, tk="M1"), ff(K.KIND_RECONCILED, tk="C1")]
    a = acct(fs)
    assert [g["key"] for g in a["groups"]] == ["differs", "dates", "unmatched", "not_compared", "matches"]
    assert [g["title"] for g in a["groups"]] == [t for _k, t, _ in jmv.ACCOUNT_GROUPS]
    got = {g["key"]: [i["ticker"] for i in g["items"]] for g in a["groups"]}
    assert got == {"differs": ["T1", "P1"], "dates": ["R1", "S1"], "unmatched": ["A1", "M1"],
                   "not_compared": ["N1", "U1"], "matches": ["L1", "C1"]}
    only = acct([ff(K.KIND_REDATE, holdings=0.0)])
    assert [g["key"] for g in only["groups"]] == ["dates"] and only["state"] == "rows"


def test_an_unknown_future_kind_is_listed_never_counted():
    f = ff(K.KIND_UNLOGGED)
    f["kind"] = "BRAND_NEW"
    a = acct([f])
    assert [g["key"] for g in a["groups"]] == ["unmatched"]
    assert a["groups"][0]["items"][0]["fix_text"] == jmv._FIX_TEXT_UNKNOWN
    assert home([f]) is None


def test_the_summary_counts_the_groups():
    fs = [shares_f("A"), price_f("B"), ff(K.KIND_REDATE, holdings=0.0), ff(K.KIND_AMBIGUOUS),
          ff(K.KIND_NOT_YET_COMPARED), ff(K.KIND_LINK), ff(K.KIND_LINK)]
    assert acct(fs)["summary_text"] == (
        "Found: 2 differ, 1 dated differently, 1 couldn't be matched automatically, 1 not compared "
        "yet. 2 agree, nothing to fix.")
    assert acct([shares_f()])["summary_text"] == "Found: 1 differs."
    only_links = acct([ff(K.KIND_LINK)])
    assert only_links["state"] == "rows"
    assert only_links["summary_text"] == "No differences found. 1 agrees, nothing to fix."


def test_item_shape_headline_and_leaves_window():
    f = ff(K.KIND_PRICE_FIX, tk="TSLA", act="SELL", holdings=0.0, jp=400.0, bp=415.28, js=5.0, bs=5.0,
           jids=[420], jdates=[date(2026, 10, 14)], fdates=[date(2026, 10, 14)], statuses=["logged"],
           txns=["T9"], pids=[7])
    it = items(acct([f]))[0]
    assert it["headline"] == "SELL 5 TSLA, journal #420, Robinhood fill Wed Oct 14"
    assert it["text"] == f["text"] and it["journal_ids"] == [420] and it["txn_ids"] == ["T9"]
    assert it["leaves_window_on"] == date(2026, 11, 19)         # Oct 14 + 35 + 1
    assert it["leaves_text"] == "Leaves this comparison on Thu Nov 19 (it covers the last 35 days)."
    assert it["status_note"] == "Pending Imports shows this fill as logged."
    assert it["kind"] == K.KIND_PRICE_FIX and it["ticker"] == "TSLA" and it["action"] == "SELL"


def test_headline_variants():
    qty = ff(K.KIND_QTY_MISMATCH, tk="GOOGL", act="SELL", holdings=1.0, js=5.0, bs=6.0, jids=[452],
             fdates=[date(2026, 10, 30)])
    assert items(acct([qty]))[0]["headline"] == "SELL 5 GOOGL (Robinhood 6), journal #452, Robinhood fill Fri Oct 30"
    unl = shares_f("GOOGL", act="BUY", bs=1.0, fdates=[date(2026, 10, 30)])
    assert items(acct([unl]))[0]["headline"] == "BUY 1 GOOGL, Robinhood fill Fri Oct 30"
    jo = ff(K.KIND_JOURNAL_ONLY, tk="X", act="BUY", holdings=5.0, js=5.0, jids=[1], jdates=[date(2026, 11, 2)],
            txns=[], pids=[], statuses=[])
    assert items(acct([jo]))[0]["headline"] == "BUY 5 X, journal #1, dated Mon Nov 2"
    many = ff(K.KIND_SPLIT, tk="X", holdings=0.0, js=3.0, jids=[1, 2], fdates=[date(2026, 10, 28), date(2026, 10, 29)])
    assert items(acct([many]))[0]["headline"] == "BUY 3 X, journal #1, #2, Robinhood fills Wed Oct 28, Thu Oct 29"
    opt = ff(K.KIND_SKIPPED_NON_EQUITY, tk="", act="", fdates=[date(2026, 11, 2)])
    assert items(acct([opt]))[0]["headline"] == "Robinhood option records, not compared"
    bare = ff(K.KIND_UNCHECKED, tk="", act="", txns=[], pids=[], statuses=[])
    assert items(acct([bare]))[0]["headline"] == "Journal vs Robinhood"


def test_status_notes():
    one = lambda st: items(acct([ff(K.KIND_UNLOGGED, holdings=1.0, statuses=[st])]))[0]["status_note"]
    assert one("pending") == "This fill is in 📥 Pending Imports."
    assert one("dismissed") == "You marked this fill 'Already logged' in Pending Imports."
    assert one("logged") == "Pending Imports shows this fill as logged."
    assert one("weird") == "Pending Imports status of this fill: weird."
    many = items(acct([ff(K.KIND_SPLIT, holdings=0.0, statuses=["pending", "dismissed", "pending"])]))[0]
    assert many["status_note"] == "Pending Imports status of the fills: 1 dismissed, 2 pending."
    assert items(acct([ff(K.KIND_JOURNAL_ONLY, holdings=1.0, statuses=[], txns=[], pids=[])]))[0]["status_note"] == ""


def test_leaves_window_on_edges():
    assert jmv._leaves_window_on(ff(K.KIND_LINK)) is None
    f = ff(K.KIND_LINK, jdates=[date(2026, 10, 12), None], fdates=[date(2026, 10, 9)])
    assert jmv._leaves_window_on(f) == date(2026, 11, 17)        # plan: a 10-12 finding leaves 11-17
    assert jmv._leaves_window_on(ff(K.KIND_LINK, fdates=[date(2026, 10, 15)])) == date(2026, 11, 20)
    assert items(acct([ff(K.KIND_LINK)]))[0]["leaves_text"] == ""


def test_window_the_matcher_keeps_today_minus_35_and_drops_today_minus_36():
    today = NOW.date()
    keep, gone = today - timedelta(days=C.JOURNAL_MATCH_LOOKBACK_DAYS), today - timedelta(days=C.JOURNAL_MATCH_LOOKBACK_DAYS + 1)
    assert keep == date(2026, 10, 8) and gone == date(2026, 10, 7)
    res = run([], [fl(1, "K1", "A", "SELL", 5, 50.0, keep, status="dismissed"),
                   fl(2, "G1", "B", "SELL", 5, 50.0, gone, status="dismissed")])
    a = acct(res)
    assert [i["ticker"] for i in items(a)] == ["A"]
    assert items(a)[0]["leaves_window_on"] == today + timedelta(days=1)
    assert a["window_text"] == f"Compared: journal rows and Robinhood fills dated {tec._fmt_date(keep)} or later."


def test_fix_text_rules():
    for kind in jbm._SEVERITY:
        t = jmv._FIX_TEXT[kind]
        low = t.lower()
        assert t and "next sync" not in low and "will fix" not in low and "delete" not in low, kind
        assert "re-log" not in low and "relog" not in low, kind
    assert set(jmv._FIX_TEXT) == set(jbm._SEVERITY)
    for kind in (K.KIND_PRICE_FIX, K.KIND_FORWARD, K.KIND_MERGED_FILLS, K.KIND_QTY_MISMATCH,
                 K.KIND_JOURNAL_ONLY):
        assert jmv.NOT_BUILT in jmv._FIX_TEXT[kind], kind
    for kind in (K.KIND_REDATE, K.KIND_SPLIT, K.KIND_FORWARD):
        assert "Check the Filled date in Robinhood." in jmv._FIX_TEXT[kind], kind
    assert jmv.NOT_BUILT == "A correction that also repairs past account figures isn't built yet."


def test_the_not_yet_compared_fix_text_names_the_lag_from_the_constant():
    t = jmv._FIX_TEXT[K.KIND_NOT_YET_COMPARED]
    assert str(C.JOURNAL_MATCH_BROKER_LAG_SESSIONS) in t and "completed transaction sync" in t
    assert "later date" in t and "after the row's date" in t
    assert "will fix" not in t and "next sync" not in t.lower()


# ── all-pending rule ─────────────────────────────────────────────────────────

def test_all_pending_findings_are_left_out_only_when_pending_imports_is_visible():
    res = run([jrow(1, "Z", "BUY", 5, 100.0, et(2026, 11, 2, 10))],
              [fl(1, "T1", "TSLA", "SELL", 5, 400.0, date(2026, 11, 10)),
               fl(2, "T2", "ORCL", "SELL", 5, 200.0, date(2026, 11, 10), status="dismissed")])
    assert sorted(f["kind"] for f in res) == ["JOURNAL_ONLY", "UNLOGGED", "UNLOGGED"]
    shown = acct(res, pending_visible=True)
    assert sorted(i["ticker"] for i in items(shown)) == ["ORCL", "Z"]      # JOURNAL_ONLY is always listed
    assert shown["in_pending_count"] == 1
    assert "1 more is in 📥 Pending Imports above." in shown["summary_text"]
    off = acct(res, pending_visible=False)
    assert sorted(i["ticker"] for i in items(off)) == ["ORCL", "TSLA", "Z"] and off["in_pending_count"] == 0
    assert "Pending Imports above" not in off["summary_text"]
    assert acct(res, pending_visible=None)["in_pending_count"] == 0       # only a real True hides


def test_a_mixed_status_finding_is_listed_and_only_pending_only_is_hidden():
    mixed = ff(K.KIND_SPLIT, holdings=0.0, statuses=["pending", "dismissed"])
    allp = ff(K.KIND_SPLIT, holdings=0.0, statuses=["pending", "pending"])
    nofill = ff(K.KIND_JOURNAL_ONLY, holdings=1.0, statuses=[], txns=[], pids=[])
    a = acct([mixed, allp, nofill], pending_visible=True)
    assert len(items(a)) == 2 and a["in_pending_count"] == 1
    only_pending = acct([allp, allp], pending_visible=True)
    # findings exist, all left out as pending: "rows" with no groups, never "clean"
    assert only_pending["state"] == "rows" and only_pending["groups"] == []
    assert only_pending["in_pending_count"] == 2
    assert only_pending["summary_text"] == "Nothing else to list here; 2 more are in 📥 Pending Imports above."
    one_pending = acct([allp], pending_visible=True)
    assert one_pending["summary_text"] == "Nothing else to list here; 1 more is in 📥 Pending Imports above."


def test_account_clean_only_for_an_empty_list_and_never_with_pending_findings():
    assert acct([])["state"] == "clean" and acct([], pending_visible=True)["state"] == "clean"
    allp = ff(K.KIND_SPLIT, holdings=0.0, statuses=["pending"])
    for fs in ([allp], [allp, allp], [allp, ff(K.KIND_LINK)], [allp, shares_f()],
               [allp, ff(K.KIND_LINK), ff(K.KIND_LINK)]):
        a = acct(fs, pending_visible=True)
        assert a["state"] == "rows", fs
        assert jmv.CLEAN_TEXT not in a["summary_text"] and "No differences found" not in a["summary_text"], fs
        assert "in 📥 Pending Imports above." in a["summary_text"], fs
    both = acct([allp, shares_f(), ff(K.KIND_LINK)], pending_visible=True)
    assert both["summary_text"] == ("Found: 1 differs. 1 agrees, nothing to fix. "
                                    "1 more is in 📥 Pending Imports above.")
    only_links = acct([allp, ff(K.KIND_LINK)], pending_visible=True)
    assert only_links["summary_text"] == "1 agrees, nothing to fix. 1 more is in 📥 Pending Imports above."


# ── UE-4: Log This Trade ─────────────────────────────────────────────────────

LOG_FIELDS = ("log_enabled", "log_primary", "log_needs_tick", "log_tick_label",
              "log_disabled_reason", "log_txn_id", "log_pending_id")


def test_the_two_tick_labels_are_pinned():
    assert jmv.LOG_TICK_LABEL_DISMISSED == (
        "I've checked: this fill isn't in my journal (I marked it Already logged earlier)")
    assert jmv.LOG_TICK_LABEL_LOGGED == (
        "I've checked: this fill isn't in my journal (it was marked logged, but no journal row is "
        "linked to it)")
    assert not hasattr(jmv, "LOG_TICK_LABEL")


@pytest.mark.parametrize("status,label", [
    ("dismissed", jmv.LOG_TICK_LABEL_DISMISSED), ("Dismissed", jmv.LOG_TICK_LABEL_DISMISSED),
    ("logged", jmv.LOG_TICK_LABEL_LOGGED), (" LOGGED ", jmv.LOG_TICK_LABEL_LOGGED)])
def test_log_is_offered_for_an_unlogged_fill_that_is_dismissed_or_logged(status, label):
    it = items(acct([shares_f(statuses=[status], txns=["T77"], pids=[42])]))[0]
    assert it["log_enabled"] is True and it["log_primary"] is False and it["log_needs_tick"] is True
    assert it["log_tick_label"] == label
    assert it["log_disabled_reason"] == "" and it["log_txn_id"] == "T77" and it["log_pending_id"] == 42
    assert pv.log_allowed(it, False) is False and pv.log_allowed(it, True) is True


def _off(it):
    assert it["log_enabled"] is False and it["log_primary"] is False and it["log_needs_tick"] is False
    assert it["log_tick_label"] == "" and it["log_disabled_reason"] == ""
    assert it["log_txn_id"] is None and it["log_pending_id"] is None
    assert pv.log_allowed(it, False) is False and pv.log_allowed(it, True) is False


@pytest.mark.parametrize("f", [
    shares_f(statuses=["pending"]),                                    # in Pending Imports: its own button
    shares_f(statuses=["something_else"]), shares_f(statuses=["failed"]),   # fail closed on any other status
    shares_f(statuses=[None]), shares_f(statuses=[""]),                # status unreadable
    shares_f(statuses=["dismissed"], txns=[""]), shares_f(statuses=["dismissed"], txns=[None]),
    shares_f(statuses=["dismissed", "dismissed"], txns=["A", "B"]),    # not exactly one fill
    shares_f(statuses=[], txns=[]),
    shares_f(statuses=["dismissed"], txns=["A", "B"]),
    ff(K.KIND_JOURNAL_ONLY, holdings=5.0, statuses=[], txns=[], pids=[]),
    ff(K.KIND_QTY_MISMATCH, holdings=1.0), linked_f(["shares"], holdings=1.0),
    price_f(), ff(K.KIND_REDATE, holdings=0.0), ff(K.KIND_LINK),
])
def test_log_is_off_for_every_other_item(f):
    _off(items(acct([f]))[0])


def test_every_item_carries_the_full_log_field_set():
    res = run(*_csco("a")) + run(*_mrvl_419())
    for it in items(acct(res)):
        for k in LOG_FIELDS:
            assert k in it, (it["kind"], k)


# ── TJDate U8b: decide_account_view ──────────────────────────────────────────

def _view(journal_rows=(), fills=(), pending_rows=None, synced=SYNC, now=NOW):
    return jmv.decide_account_view(frame(*journal_rows), list(fills), synced, now, pending_rows)


def _two_dismissed_sells():
    return [fl(1, "T1", "TSLA", "SELL", 5, 400.0, date(2026, 11, 10), status="dismissed"),
            fl(2, "T2", "ORCL", "SELL", 5, 200.0, date(2026, 11, 10), status="dismissed")]


def _by_ticker(view, tk):
    got = [i for i in items(view) if i["ticker"] == tk]
    assert len(got) == 1, (tk, [i["ticker"] for i in items(view)])
    return got[0]


def test_account_view_offline_names_the_unreadable_part():
    for j, f, words in ((None, [], "your journal couldn't"),
                        (frame(), None, "Robinhood's fills couldn't"),
                        (None, None, "your journal and Robinhood's fills couldn't")):
        v = jmv.decide_account_view(j, f, SYNC, NOW, None)
        assert v["state"] == "offline" and words in v["offline_text"], (j, f, v["offline_text"])
        assert "the comparison didn't run" not in v["offline_text"]
        assert v["groups"] == [] and v["in_pending_count"] == 0
        assert_plain(v)


def test_account_view_a_matcher_failure_reads_didnt_run_and_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("matcher down")
    monkeypatch.setattr(jmv._jbm, "match_journal_to_broker", boom)
    v = _view(fills=_two_dismissed_sells())
    assert v["state"] == "offline" and "the comparison didn't run this render" in v["offline_text"]
    monkeypatch.setattr(jmv._jbm, "match_journal_to_broker", lambda *a, **k: {"not": "a list"})
    v = _view(fills=_two_dismissed_sells())
    assert v["state"] == "offline" and "the comparison didn't run this render" in v["offline_text"]
    monkeypatch.setattr(jmv._jbm, "match_journal_to_broker", lambda *a, **k: None)
    assert _view()["state"] == "offline"


def test_account_view_a_naive_now_is_a_value_error_even_when_offline():
    with pytest.raises(ValueError):
        jmv.decide_account_view(None, None, SYNC, datetime(2026, 11, 12, 12, 0), None)


@pytest.mark.parametrize("pending_rows", [None, []])
def test_account_view_without_pending_rows_lists_the_all_pending_findings(pending_rows):
    fills = [fl(1, "T1", "TSLA", "SELL", 5, 400.0, date(2026, 11, 10), status="pending")]
    v = _view(fills=fills, pending_rows=pending_rows)
    assert v["state"] == "rows" and v["in_pending_count"] == 0
    assert [i["ticker"] for i in items(v)] == ["TSLA"]
    assert items(v)[0]["log_enabled"] is False          # a pending fill gets no Log here


def test_account_view_all_findings_pending_is_rows_with_no_groups_never_no_differences():
    fills = [fl(1, "T1", "TSLA", "SELL", 5, 400.0, date(2026, 11, 10), status="pending")]
    v = _view(fills=fills, pending_rows=[{"pending_id": 1, "twin_txn_id": None}])
    assert v["state"] == "rows" and v["groups"] == [] and v["in_pending_count"] == 1
    assert "No differences found" not in v["summary_text"]
    assert "1 more is in 📥 Pending Imports above." in v["summary_text"]


def test_account_view_a_twin_used_by_pending_imports_gets_no_log_of_its_own():
    v = _view(fills=_two_dismissed_sells(),
              pending_rows=[{"twin_txn_id": "T1"}, {"twin_txn_id": None}])
    tw, ok = _by_ticker(v, "TSLA"), _by_ticker(v, "ORCL")
    assert tw["log_enabled"] is False and tw["log_needs_tick"] is False
    assert tw["log_tick_label"] == "" and tw["log_prefill"] is None
    assert tw["log_disabled_reason"] == jmv.TWIN_LOG_OFF_REASON == (
        "a matching fill in 📥 Pending Imports above offers Log for the same trade; log it from "
        "there so it isn't logged twice")
    assert tw["log_twin_suppressed"] is True
    assert pv.log_allowed(tw, True) is False and pv.log_allowed(tw, False) is False
    # a non-twin UNLOGGED dismissed item keeps Log on, with the tick
    assert ok["log_enabled"] is True and ok["log_needs_tick"] is True
    assert ok["log_tick_label"] == jmv.LOG_TICK_LABEL_DISMISSED and ok["log_twin_suppressed"] is False
    assert pv.log_allowed(ok, False) is False and pv.log_allowed(ok, True) is True
    assert_plain(v)


def test_account_view_twin_ids_are_ignored_when_pending_rows_is_not_a_list_or_has_junk():
    fills = _two_dismissed_sells()
    for rows in (None, [], ["junk", 3, None], [{"twin_txn_id": ""}, {}]):
        v = _view(fills=fills, pending_rows=rows)
        assert all(i["log_twin_suppressed"] is False for i in items(v)), rows
        assert _by_ticker(v, "TSLA")["log_enabled"] is True


def test_account_view_log_prefill_comes_from_the_fills_record():
    it = _by_ticker(_view(fills=_two_dismissed_sells()), "TSLA")
    assert it["log_prefill"] == {"ticker": "TSLA", "action": "SELL", "shares": 5, "price": 400.0,
                                 "trade_date": "2026-11-10", "snaptrade_txn_id": "T1",
                                 "pending_id": 1}


def test_account_view_every_item_has_a_prefill_key_that_is_none_when_log_is_off():
    j = [jrow(1, "Z", "BUY", 5, 100.0, et(2026, 11, 2, 10))]            # a JOURNAL_ONLY row
    v = _view(journal_rows=j, fills=_two_dismissed_sells())
    kinds = {i["kind"] for i in items(v)}
    assert "JOURNAL_ONLY" in kinds and "UNLOGGED" in kinds
    for it in items(v):
        assert "log_prefill" in it and "log_twin_suppressed" in it
        assert (it["log_prefill"] is not None) == it["log_enabled"]


def _fake_unlogged(txn="T1", pid=1):
    return ff(K.KIND_UNLOGGED, tk="TSLA", act="SELL", holdings=5.0, statuses=["dismissed"],
              txns=[txn], pids=[pid] if pid is not None else [], bs=5, bp=400.0,
              fdates=[date(2026, 11, 10)])


def test_account_view_a_missing_or_duplicated_fill_record_turns_log_off(monkeypatch):
    rec = fl(1, "T1", "TSLA", "SELL", 5, 400.0, date(2026, 11, 10), status="dismissed")
    cases = (
        ([_fake_unlogged("T9", 9)], [rec]),                         # by id: no record
        ([_fake_unlogged("T9", None)], [rec]),                      # by txn: no record
        ([_fake_unlogged("T1", 1)], [rec, dict(rec)]),              # by id: two records
        ([_fake_unlogged("T1", None)], [rec, dict(rec, id=2)]),     # by txn: two records
    )
    for findings, fills in cases:
        monkeypatch.setattr(jmv._jbm, "match_journal_to_broker", lambda *a, _f=findings, **k: _f)
        it = items(_view(fills=fills))[0]
        assert it["log_enabled"] is False and it["log_needs_tick"] is False, findings
        assert it["log_tick_label"] == "" and it["log_prefill"] is None
        assert it["log_disabled_reason"] == (
            "Robinhood's record for this fill couldn't be found this render")
        assert it["log_twin_suppressed"] is False
        assert pv.log_allowed(it, True) is False
    # the same finding with exactly one record is fine, by id and by txn
    for findings in ([_fake_unlogged("T1", 1)], [_fake_unlogged("T1", None)]):
        monkeypatch.setattr(jmv._jbm, "match_journal_to_broker", lambda *a, _f=findings, **k: _f)
        it = items(_view(fills=[rec]))[0]
        assert it["log_enabled"] is True and it["log_prefill"]["snaptrade_txn_id"] == "T1"


def test_account_view_clean_journal_and_fills_is_the_clean_state():
    v = _view()
    assert v["state"] == "clean" and v["groups"] == [] and "No differences found" in v["summary_text"]


# ── sync wording (UE-8) ──────────────────────────────────────────────────────

def test_sync_status_text_states():
    t = et(2026, 11, 12, 9, 5)
    assert jmv.sync_status_text(t, t) == "Last sync run: Thu Nov 12, 9:05 AM ET. Transactions imported in that run."
    older = et(2026, 11, 11, 16, 30)
    assert jmv.sync_status_text(t, older) == (
        "Last sync run: Thu Nov 12, 9:05 AM ET. Its transaction import didn't finish; fills are compared "
        "through the Wed Nov 11, 4:30 PM ET sync.")
    assert jmv.sync_status_text(t, None) == (
        "Last sync run: Thu Nov 12, 9:05 AM ET. No completed transaction import on record (or it "
        "couldn't be read), so journal rows aren't compared with Robinhood's fills.")
    assert jmv.sync_status_text(None, None) == "Waiting for the first `broker` cron sync."
    # txn newer than full: "last run" uses the later stamp, and the import counts as done
    assert jmv.sync_status_text(older, t) == "Last sync run: Thu Nov 12, 9:05 AM ET. Transactions imported in that run."
    assert jmv.sync_status_text(None, older).startswith("Last sync run: Wed Nov 11, 4:30 PM ET.")
    assert "couldn't be read" in jmv.sync_status_text("garbage", None)
    assert "couldn't be read" in jmv.sync_status_text("garbage", "garbage")


def test_sync_status_parts_each_branch_and_text_is_their_join():
    t = et(2026, 11, 12, 9, 5)
    older = et(2026, 11, 11, 16, 30)
    run_t = "Last sync run: Thu Nov 12, 9:05 AM ET."
    cases = [
        # equal stamps: the import finished
        ((t, t), {"run_text": run_t, "txn_text": "Transactions imported in that run.", "txn_ok": True}),
        # txn older than the full run: did not finish
        ((t, older), {"run_text": run_t, "txn_ok": False,
                      "txn_text": "Its transaction import didn't finish; fills are compared "
                                  "through the Wed Nov 11, 4:30 PM ET sync."}),
        # txn None with a full stamp set
        ((t, None), {"run_text": run_t, "txn_text": jmv.NO_TXN_SYNC_TEXT, "txn_ok": False}),
        # both None: waiting, nothing to warn about
        ((None, None), {"run_text": jmv.WAITING_FOR_SYNC_TEXT, "txn_text": "", "txn_ok": True}),
        # unreadable stamps
        (("garbage", None), {"run_text": "Last sync run: time couldn't be read.",
                             "txn_text": jmv.NO_TXN_SYNC_TEXT, "txn_ok": False}),
        (("garbage", "garbage"), {"run_text": "Last sync run: time couldn't be read.",
                                  "txn_text": jmv.NO_TXN_SYNC_TEXT, "txn_ok": False}),
    ]
    for args, want in cases:
        got = jmv.sync_status_parts(*args)
        assert got == want, (args, got)
        assert jmv.sync_status_text(*args) == " ".join(p for p in (got["run_text"], got["txn_text"]) if p)


def test_sync_status_text_dst_change_reads_the_same_wall_clock_in_et():
    assert jmv.sync_status_text("2026-10-31T13:05:00Z", "2026-10-31T13:05:00Z").startswith(
        "Last sync run: Sat Oct 31, 9:05 AM ET.")                          # EDT
    assert jmv.sync_status_text("2026-11-02T14:05:00Z", "2026-11-02T14:05:00Z").startswith(
        "Last sync run: Mon Nov 2, 9:05 AM ET.")                           # EST
    assert jmv.sync_status_text("2026-11-12T14:05:00+00:00", None).startswith("Last sync run: Thu Nov 12, 9:05 AM ET.")


# ── time-context matrix (real matcher) ───────────────────────────────────────

def _unlogged_sell(now, sync, placed, status="dismissed"):
    res = run([], [fl(1, "T1", "TSLA", "SELL", 5, 400.0, placed, status=status)], synced=sync, now=now)
    assert [f["kind"] for f in res] == ["UNLOGGED"]
    return res


def test_saturday_and_thanksgiving_visits_still_count_an_unlogged_sell():
    for now, sync, placed in ((et(2026, 11, 14, 12), et(2026, 11, 13, 16, 30), date(2026, 11, 12)),
                              (et(2026, 11, 26, 12), et(2026, 11, 25, 16), date(2026, 11, 24))):
        res = _unlogged_sell(now, sync, placed)
        line = jmv.home_line(res, None, sync, sync, True)
        assert line["tier"] == "warning" and line["counted"] == 1
        a = jmv.account_list(res, sync, now, False, ())
        assert a["state"] == "rows" and a["groups"][0]["key"] == "differs"
        assert a["window_text"].startswith("Compared: journal rows and Robinhood fills dated")


def test_morning_and_evening_on_one_et_date_give_identical_output_and_memo_key():
    j = [jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 3, 10))]
    f = [fl(1, "T1", "X", "BUY", 5, 100.5, date(2026, 11, 3), status="dismissed")]
    outs, keys = [], []
    for now in (et(2026, 11, 12, 7, 0), et(2026, 11, 12, 20, 30)):
        res = run(j, f, now=now)
        outs.append((jmv.home_line(res, None, SYNC, SYNC, True), jmv.account_list(res, SYNC, now, False, ())))
        keys.append(jmv.memo_key(tec._aware_et(now).date(), jmv.journal_signature(frame(*j)), SYNC, SYNC, 0))
    assert outs[0] == outs[1] and keys[0] == keys[1]
    assert outs[0][0]["counted"] == 1


def test_the_et_midnight_changes_the_memo_key_not_the_utc_midnight():
    sig = jmv.journal_signature(frame())
    late = datetime(2026, 11, 13, 4, 30, tzinfo=timezone.utc)            # 23:30 ET on 11-12
    after = et(2026, 11, 13, 0, 30)
    k_late = jmv.memo_key(tec._aware_et(late).date(), sig, SYNC, SYNC, 0)
    k_after = jmv.memo_key(tec._aware_et(after).date(), sig, SYNC, SYNC, 0)
    assert k_late[0] == "2026-11-12" and k_after[0] == "2026-11-13" and k_late != k_after
    assert jmv.memo_key(tec._aware_et(late).date(), sig, SYNC, SYNC, 0) == k_late


def test_a_six_day_old_sync_still_counts_an_unlogged_and_makes_no_clean_claim():
    sync = NOW - timedelta(days=6)
    res = _unlogged_sell(NOW, sync, date(2026, 11, 10))
    assert jmv.home_line(res, None, sync, sync, True)["tier"] == "warning"
    a = jmv.account_list(res, sync, NOW, False, ())
    assert a["state"] == "rows" and a["coverage_text"] == jbm.match_coverage(sync)["text"]
    assert "Journal rows dated after" in a["coverage_text"]
    clean = jmv.account_list([], sync, NOW, False, ())
    assert clean["summary_text"] == "No differences found." and "Journal rows dated after" in clean["coverage_text"]


@pytest.mark.parametrize("act", ["BUY", "SELL"])
def test_a_trade_logged_a_day_late_is_redate_listed_under_dates_and_not_counted(act):
    j = [jrow(1, "X", act, 5, 100.0, et(2026, 11, 3, 10))]                 # journal 11-03, fill placed 11-02
    f = [fl(1, "T1", "X", act, 5, 100.0, date(2026, 11, 2), status="dismissed")]
    res = run(j, f)
    assert [x["kind"] for x in res] == ["REDATE"]
    assert home(res) is None
    a = acct(res)
    assert [g["key"] for g in a["groups"]] == ["dates"]
    assert "Check the Filled date in Robinhood." in items(a)[0]["fix_text"]
    _off(items(a)[0])


@pytest.mark.parametrize("act", ["BUY", "SELL"])
def test_a_back_dated_trade_is_forward_counted_as_a_date_difference(act):
    j = [jrow(1, "X", act, 5, 100.0, et(2026, 11, 2, 10))]                 # journal 11-02, fill placed 11-03
    f = [fl(1, "T1", "X", act, 5, 100.0, date(2026, 11, 3), status="dismissed")]
    res = run(j, f)
    assert [x["kind"] for x in res] == ["FORWARD"]
    line = home(res)
    assert line["tier"] == "caption" and line["counted"] == 1 and "(0 price, 1 date)" in line["text"]
    assert [g["key"] for g in acct(res)["groups"]] == ["differs"]


def test_a_naive_now_is_a_value_error_even_when_offline():
    naive = datetime(2026, 11, 12, 12, 0)
    for fs in (None, [], [shares_f()]):
        with pytest.raises(ValueError):
            jmv.account_list(fs, SYNC, naive, False, ())


def test_end_to_end_real_fixtures_are_plain_and_non_empty():
    j, f = _444_rows()
    res = run(j, f) + run(*_csco("a")) + run(*_mrvl_419())
    a = acct(res)
    assert a["state"] == "rows" and items(a)
    for it in items(a):
        assert it["headline"] and it["text"] and it["fix_text"], it["kind"]
    for line in (home(res), home(res, drift(qty_mismatch=[{"ticker": "GOOGL"}]))):
        assert line["text"]
        assert_plain(line)
    assert_plain(a)
    assert_plain([acct(None, parts=("journal",)), acct([]), jmv.sync_status_text(SYNC, SYNC)])
    assert_plain([jmv.HOME_UNCHECKED_TEXT, jmv.LOG_TICK_LABEL_DISMISSED, jmv.LOG_TICK_LABEL_LOGGED, jmv.NOT_BUILT, list(jmv._FIX_TEXT.values())])


# ── memo helpers ─────────────────────────────────────────────────────────────

def _jdf(rows=None):
    base = [jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 3, 10), txn="T1"),
            jrow(2, "Y", "SELL", 2.5, 50.123456, et(2026, 11, 4, 11))]
    return frame(*(base if rows is None else rows))


def test_journal_signature_is_order_independent_and_sensitive():
    base = jmv.journal_signature(_jdf())
    assert isinstance(base, str) and len(base) == 64
    rows = list(_jdf().to_dict("records"))
    assert jmv.journal_signature(pd.DataFrame(rows[::-1])) == base
    assert jmv.journal_signature(_jdf().iloc[::-1].reset_index(drop=True)) == base

    def changed(**over):
        r = [dict(x) for x in rows]
        r[1].update(over)
        return jmv.journal_signature(pd.DataFrame(r))

    assert changed(broker_txn_id="T2") != base                        # a broker_txn_id backfill
    assert changed(price=50.2) != base                                # a price edit
    assert changed(shares=3.0) != base and changed(ticker="Z") != base and changed(action="BUY") != base
    assert changed(id=9) != base
    assert changed(traded_at=et(2026, 11, 4, 12).isoformat()) != base
    assert changed(notes="edited") == base                            # notes aren't part of the signature (the matcher reads them only through trade_time.normalize_traded_at)
    assert jmv.journal_signature(pd.DataFrame(rows[:1])) != base      # a deleted row
    assert jmv.journal_signature(pd.DataFrame(rows + rows[:1])) != base
    assert changed(price=50.1234560001) == base                       # price to 6 dp
    assert changed(shares=2.5000000001) == base                       # shares to 9 dp


def test_journal_signature_none_tokens_and_timestamps_and_missing_columns():
    base = jmv.journal_signature(_jdf())
    tokens = [jmv.journal_signature(_jdf([jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 3, 10), txn=t)]))
              for t in (None, "", "None", float("nan"), "nan")]
    assert len(set(tokens)) == 1 and tokens[0] != base
    same_instant = ("2026-11-03T15:00:00Z", "2026-11-03T10:00:00-05:00", "2026-11-03T15:00:00+00:00")
    sigs = {jmv.journal_signature(_jdf([jrow(1, "X", "BUY", 5, 100.0, t)])) for t in same_instant}
    assert len(sigs) == 1
    junk = jmv.journal_signature(_jdf([jrow(1, "X", "BUY", 5, 100.0, "not a time")]))
    assert isinstance(junk, str)
    miss = jmv.journal_signature(pd.DataFrame([{"id": 1, "ticker": "X"}]))
    none_cols = jmv.journal_signature(pd.DataFrame([{"id": 1, "ticker": "X", "action": None, "shares": None,
                                                     "price": float("nan"), "traded_at": "",
                                                     "broker_txn_id": "None"}]))
    assert miss == none_cols


def test_journal_signature_empty_and_non_frames():
    a, b = jmv.journal_signature(frame()), jmv.journal_signature(pd.DataFrame())
    assert isinstance(a, str) and a == b
    for bad in (None, [], {}, "x", 3, [{"id": 1}]):
        assert jmv.journal_signature(bad) is None


def test_memo_key_shape_and_each_component_changes_it():
    sig = jmv.journal_signature(_jdf())
    d = date(2026, 11, 12)
    base = jmv.memo_key(d, sig, SYNC, SYNC, 0)
    assert isinstance(base, tuple) and len(base) == 5
    assert base == ("2026-11-12", sig, "2026-11-12T09:00:00-05:00", "2026-11-12T09:00:00-05:00", 0)
    assert jmv.memo_key(d + timedelta(days=1), sig, SYNC, SYNC, 0) != base
    assert jmv.memo_key(d, "other", SYNC, SYNC, 0) != base
    assert jmv.memo_key(d, sig, SYNC + timedelta(minutes=1), SYNC, 0) != base
    assert jmv.memo_key(d, sig, SYNC, SYNC + timedelta(minutes=1), 0) != base
    assert jmv.memo_key(d, sig, SYNC, SYNC, 1) != base
    assert jmv.memo_key(d, sig, None, None, 0) == ("2026-11-12", sig, None, None, 0)
    assert jmv.memo_key(d, sig, "garbage", None, 0)[2] is None
    assert jmv.memo_key(d, None, SYNC, SYNC, 0)[1] is None


def test_memo_key_two_offsets_of_one_instant_are_one_key():
    sig = "s"
    a = jmv.memo_key(date(2026, 11, 12), sig, "2026-11-12T14:00:00Z", "2026-11-12T14:00:00Z", 0)
    b = jmv.memo_key(date(2026, 11, 12), sig, "2026-11-12T09:00:00-05:00", "2026-11-12T15:00:00+01:00", 0)
    c = jmv.memo_key(date(2026, 11, 12), sig, datetime(2026, 11, 12, 14, 0, tzinfo=timezone.utc),
                     et(2026, 11, 12, 9, 0), 0)
    assert a == b == c
    assert jmv.memo_key(date(2026, 11, 12), sig, "2026-11-12T14:00:00", "2026-11-12T14:00:00", 0) == a   # naive = UTC


@pytest.mark.parametrize("bad", [datetime(2026, 11, 12, 12, 0), et(2026, 11, 12, 12), "2026-11-12", None, 5])
def test_memo_key_requires_a_date(bad):
    with pytest.raises(ValueError):
        jmv.memo_key(bad, "s", SYNC, SYNC, 0)


# ── purity and reuse ─────────────────────────────────────────────────────────

def _imports(path):
    mods = set()
    for n in ast.walk(ast.parse(Path(path).read_text(encoding="utf-8"))):
        if isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add(n.module)
            mods |= {f"{n.module}.{a.name}" for a in n.names}
    return mods


def test_module_is_pure_no_streamlit_db_data_import_and_no_clock_read():
    mods = _imports(jmv.__file__)
    assert not any(m and (m.startswith("streamlit") or m in ("stock_analyzer.db", "stock_analyzer.data"))
                   for m in mods), mods
    own = {m for m in mods if m and m.startswith("stock_analyzer")}
    assert own <= {"stock_analyzer", "stock_analyzer.journal_broker_match",
                   "stock_analyzer.trade_entry_checks", "stock_analyzer.constants",
                   "stock_analyzer.constants.JOURNAL_MATCH_LOOKBACK_DAYS",
                   "stock_analyzer.constants.JOURNAL_MATCH_BROKER_LAG_SESSIONS"}, own
    tree = ast.parse(Path(jmv.__file__).read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute):
            assert n.attr not in ("now", "today", "utcnow", "utcfromtimestamp", "fromtimestamp"), n.attr
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            assert not (n.func.attr == "time" and isinstance(n.func.value, ast.Name)
                        and n.func.value.id == "time")


def test_helpers_are_reused_not_copied():
    assert jmv._jbm is jbm and jmv._tec is tec
    assert jmv._jbm._sync_et is jbm._sync_et and jmv._jbm._sync_when is jbm._sync_when
    assert jmv._jbm.match_coverage is jbm.match_coverage
    assert jmv._tec._same_shares is tec._same_shares and jmv._tec._same_price is tec._same_price
    assert jmv._tec._fmt_date is tec._fmt_date and jmv._tec._aware_et is tec._aware_et
    assert jmv.JOURNAL_MATCH_LOOKBACK_DAYS == C.JOURNAL_MATCH_LOOKBACK_DAYS
    src = Path(jmv.__file__).read_text(encoding="utf-8")
    assert "or []" not in src and "or {}" not in src                  # the offline-sentinel antipattern


def test_matcher_texts_for_every_fixture_carry_no_dollar_sign():
    j, f = _444_rows()
    for res in (run(j, f), run(*_csco("a")), run(*_mrvl_419())):
        for r in res:
            assert "$" not in r["text"] and "**" not in r["text"]


# ── property: the seeded generator ───────────────────────────────────────────

def _key(x):
    """(kind, journal ids, non-empty txn ids) of a finding or an Account item."""
    return (x["kind"], tuple(x["journal_ids"]), tuple(t for t in x["txn_ids"] if t))


def test_property_counts_and_coverage_over_the_matchers_seeded_cases():
    rng = random.Random(7)
    seen = set()
    for case in range(120):
        now = et(2026, 11, rng.randint(10, 25), 12)
        sync = rng.choice([None, now, now - timedelta(days=1), now - timedelta(days=3), now - timedelta(days=6)])
        rows, fills = _fuzz_case(rng, now)
        res = jbm.match_journal_to_broker(frame(*rows), fills, sync, now)
        assert res is not None, case
        seen |= {f["kind"] for f in res}
        verdict = rng.choice([None, drift(qty_mismatch=[{"ticker": "A"}], rh_only=[{"ticker": "B"}]),
                              drift("awaiting_sync", qty_mismatch=[{"ticker": "A"}]), drift()])
        counted = [f for f in res if f["kind"] in jmv.HOME_COUNTED_KINDS]
        line = jmv.home_line(res, verdict, sync, now, True)
        drift_tk = ({t["ticker"] for k in ("qty_mismatch", "rh_only")
                     for t in verdict["diff"].get(k, [])} if (verdict or {}).get("state") == "drift"
                    else set())
        deferred = [f for f in counted if jmv.changes_holdings(f) and f["ticker"] in drift_tk
                    and not (f["kind"] == "LINKED_DIFFERS"
                             and {"ticker", "action"} & set(f["differs_on"]))]
        kept_shares = [f for f in counted if jmv.changes_holdings(f) and f not in deferred]
        if counted:
            assert line is not None, case
            assert line["counted"] + line["deferred"] == len(counted), case
            assert line["deferred"] == len(deferred), case
            assert (line["tier"] == "warning") == bool(kept_shares), case
            assert line["text"]
            assert_plain(line)
        else:
            assert line is None or line["counted"] == 0 == line["deferred"], case
        shuffled = list(res)
        rng.shuffle(shuffled)
        again = jmv.home_line(shuffled, verdict, sync, now, True)
        assert (again or {}).get("counted") == (line or {}).get("counted"), case
        for visible in (False, True):
            a = jmv.account_list(res, sync, now, visible, ())
            n_pending = sum(1 for f in res if f["fill_statuses"] and all(s == "pending" for s in f["fill_statuses"]))
            assert len(items(a)) + a["in_pending_count"] == len(res), (case, visible)
            assert a["in_pending_count"] == (n_pending if visible else 0), (case, visible)
            assert a["state"] == ("rows" if res else "clean"), (case, visible)
            if a["in_pending_count"]:
                assert jmv.CLEAN_TEXT not in a["summary_text"], case
            pending_in = [f for f in res if f["fill_statuses"]
                          and all(s == "pending" for s in f["fill_statuses"])]
            got = Counter(_key(i) for i in items(a)) + Counter(
                _key(f) for f in (pending_in if visible else []))
            assert got == Counter(_key(f) for f in res), (case, visible)
            assert_plain(a)
            for it in items(a):
                assert it["headline"] and it["text"] and it["fix_text"]
                assert it["log_enabled"] is False or (
                    it["kind"] == "UNLOGGED" and it["log_needs_tick"] and it["log_txn_id"])
    assert {"UNLOGGED", "LINK", "NOT_YET_COMPARED", "JOURNAL_ONLY"} <= seen


def test_property_every_finding_lands_in_exactly_one_group_over_real_kinds():
    rng = random.Random(11)
    now = et(2026, 11, 20, 12)
    for case in range(60):
        rows, fills = _fuzz_case(rng, now)
        res = jbm.match_journal_to_broker(frame(*rows), fills, now, now)
        a = jmv.account_list(res, now, now, False, ())
        assert Counter(_key(i) for i in items(a)) == Counter(_key(f) for f in res), case
        for g in a["groups"]:
            allowed = {k for key, _t, kinds in jmv.ACCOUNT_GROUPS if key == g["key"] for k in kinds}
            assert all(i["kind"] in allowed for i in g["items"]), case


# ── the real U9 -> U8 chain: one Log offered per unlogged fill, never two ────

def _real_chain(statuses):
    """One journal BUY (5 X @ 100.00, 11-03) and identical broker fills with `statuses`,
    through the real U9 decision, whose rows feed the real U8 view."""
    fills = [fl(i, f"T{i}", "X", "BUY", 5, 100.0, date(2026, 11, 3), status=s)
             for i, s in enumerate(statuses, start=1)]
    pending = [f for f in fills if f["status"] == "pending"]
    journal = frame(jrow(1, "X", "BUY", 5, 100.0, et(2026, 11, 3, 10)))
    rows = pv.decide_pending_imports(pending, journal, fills, SYNC, NOW)["rows"]
    view = jmv.decide_account_view(journal, fills, SYNC, NOW, rows)
    return rows, items(view)


def test_real_chain_pending_plus_one_identical_dismissed_fill_offers_one_log():
    rows, its = _real_chain(["pending", "dismissed"])
    u9_logs = [r for r in rows if r["log_enabled"]]
    u8_logs = [i for i in its if i["log_enabled"]]
    assert len(u9_logs) == 1 and len(u8_logs) == 0
    assert len(its) == 1 and its[0]["log_twin_suppressed"] is True


def test_real_chain_pending_plus_two_identical_dismissed_fills_offers_two_logs():
    rows, its = _real_chain(["pending", "dismissed", "dismissed"])
    u9_logs = [r for r in rows if r["log_enabled"]]
    u8_logs = [i for i in its if i["log_enabled"]]
    assert len(u9_logs) == 1 and len(u8_logs) == 1
    assert len(u9_logs) + len(u8_logs) == 2
    assert sum(1 for i in its if i["log_twin_suppressed"]) == 1

