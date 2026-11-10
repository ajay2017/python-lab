"""TJDate C2b: the confirm card's tick-box wording follows the finding group, and the
app.py wiring of the price / broker checks (source / AST pins: no test imports app.py).

The card keeps its ONE box. A tick finding in group "time" (or with no group) reads
exactly as before ("Check the date and time" / "The date and time are right"); once a
"price" or "broker" tick is among them the box vouches for the whole entry.
"""
import ast
from datetime import date

from stock_analyzer import trade_confirm as tc
from stock_analyzer import trade_entry_checks as tec
from tests._ast_cache import parse_file_cached
from tests.test_trade_confirm import (
    _APP, _TICK_F, _TICK_G, _app_text, _dups, _slice,
)

_TIME_G = {**_TICK_F, "group": "time"}
_PRICE_T = {"kind": "PRICE_OUTSIDE_RANGE", "severity": "tick", "group": "price",
            "text": "$171.88 is outside Mon Nov 2's trading range ($186.10 – $191.40, with 1% leeway)"}
_BROKER_T = {"kind": "PRICE_DIFFERS", "severity": "tick", "group": "broker",
             "text": "Robinhood's record of BUY 3 OKTA on Mon Nov 2 is at 189.14, not 171.88. "
                     "Check the price in Robinhood."}
_BROKER_INFO = {"kind": "BROKER_NOT_SYNCED", "severity": "info", "group": "broker",
                "text": "Not compared with Robinhood's fills for Tue Nov 10 yet."}

_OLD_TITLE = "Check the date and time"
_OLD_HINT = "Tick the box below if the date and time are right (for example an extended-hours fill)."
_OLD_LABEL = "The date and time are right"
_NEW_TITLE = "Check this entry"
_NEW_HINT = "Tick the box below if the entry is right as typed (for example an extended-hours fill)."
_NEW_LABEL = "The entry is right as typed"


def test_wording_constants_are_exactly_the_agreed_text():
    assert (tc.TITLE_ENTRY_TICK, tc.LINE_ENTRY_TICK_HINT, tc.LABEL_ENTRY_TICK) == \
        (_OLD_TITLE, _OLD_HINT, _OLD_LABEL)
    assert (tc.TITLE_ENTRY_TICK_WIDE, tc.LINE_ENTRY_TICK_HINT_WIDE, tc.LABEL_ENTRY_TICK_WIDE) == \
        (_NEW_TITLE, _NEW_HINT, _NEW_LABEL)


def test_time_group_and_ungrouped_ticks_keep_the_date_and_time_wording():
    for ticks in ([_TICK_F], [_TIME_G], [_TICK_G, _TIME_G],
                  [{"kind": "X", "severity": "weird", "text": "odd"}]):
        g = tc.confirm_gate(_dups()["clean"], entry_findings=ticks)
        assert g["tick_label"] == _OLD_LABEL
        warn = [b for b in g["blocks"] if b["level"] == "warning"]
        assert warn[0]["title"] == _OLD_TITLE and warn[0]["lines"][-1] == _OLD_HINT


def test_price_tick_uses_the_whole_entry_wording():
    g = tc.confirm_gate(_dups()["clean"], entry_findings=[_PRICE_T])
    assert g["needs_tick"] is True and g["hard"] is False and g["tick_label"] == _NEW_LABEL
    assert g["blocks"] == [{"level": "warning", "title": _NEW_TITLE,
                            "lines": [_PRICE_T["text"], _NEW_HINT]}]


def test_broker_tick_and_a_mix_with_time_use_the_whole_entry_wording():
    g = tc.confirm_gate(_dups()["clean"], entry_findings=[_BROKER_T])
    assert g["tick_label"] == _NEW_LABEL and g["blocks"][0]["title"] == _NEW_TITLE
    mixed = tc.confirm_gate(_dups()["clean"], entry_findings=[_TIME_G, _PRICE_T, _BROKER_T])
    assert mixed["tick_label"] == _NEW_LABEL
    warn = [b for b in mixed["blocks"] if b["level"] == "warning"]
    assert len(warn) == 1 and warn[0]["title"] == _NEW_TITLE        # still ONE box, ONE block
    assert warn[0]["lines"] == [_TIME_G["text"], _PRICE_T["text"], _BROKER_T["text"], _NEW_HINT]


def test_combined_label_with_a_duplicate_tick_names_the_entry():
    soft = _dups()["soft"]
    dup_only = tc.confirm_gate(soft)
    g = tc.confirm_gate(soft, entry_findings=[_PRICE_T])
    assert g["tick_label"] == f"{dup_only['tick_label']}, and the entry is right as typed"
    assert [b["level"] for b in g["blocks"]] == ["warning", "warning"]
    assert g["blocks"][1]["title"] == _NEW_TITLE
    # a time-only tick beside the same duplicate keeps the old combined label
    assert tc.confirm_gate(soft, entry_findings=[_TIME_G])["tick_label"] == \
        f"{dup_only['tick_label']}, and the date and time are right"


def test_info_and_hard_findings_in_price_or_broker_groups_never_change_the_wording():
    clean = _dups()["clean"]
    g = tc.confirm_gate(clean, entry_findings=[_BROKER_INFO])
    assert g["needs_tick"] is False and g["tick_label"] == ""
    assert g["blocks"] == [{"level": "info", "title": _BROKER_INFO["text"], "lines": []}]
    g = tc.confirm_gate(clean, entry_findings=[_TIME_G, _BROKER_INFO])
    assert g["tick_label"] == _OLD_LABEL                            # the only tick is a time one
    hard_price = {"kind": "X", "severity": "hard", "group": "price", "text": "no"}
    g = tc.confirm_gate(clean, entry_findings=[hard_price, _TIME_G])
    assert g["hard"] is True and g["tick_label"] == _OLD_LABEL


def test_price_tick_beside_an_exact_identity_duplicate_has_no_box_and_no_hint():
    g = tc.confirm_gate(_dups()["hard"], entry_findings=[_PRICE_T])
    assert g["hard"] is True and g["needs_tick"] is False and g["tick_label"] == ""
    assert g["blocks"][1] == {"level": "warning", "title": _NEW_TITLE, "lines": [_PRICE_T["text"]]}


def test_wide_wording_is_plain_text_and_the_c2b_findings_are_info_or_tick():
    g = tc.confirm_gate(_dups()["soft"], entry_findings=[_PRICE_T, _BROKER_T, _BROKER_INFO])
    texts = [g["tick_label"]]
    for b in g["blocks"]:
        texts += [b["title"], *b["lines"]]
    assert all("<" not in x and ">" not in x for x in texts)
    band = tec.price_band_finding(171.88, date(2026, 11, 2), {"low": 186.1, "high": 191.4},
                                  date(2026, 11, 9))
    built = [tec.price_band_entry_finding(band, 171.88, date(2026, 11, 2)),
             tec.price_check_failed_finding(), tec.broker_unchecked_finding(),
             tec.broker_coverage_finding(tec.broker_check_coverage(date(2026, 11, 10), None))]
    assert all(f["severity"] in ("tick", "info") and f["group"] in ("price", "broker") for f in built)


# ── app.py wiring pins ───────────────────────────────────────────────────────

def test_price_sanity_decision_moved_out_of_app():
    t = _app_text()
    assert "TRADE_PRICE_SANITY_RATIO" not in t
    assert "price_val / _market_price" not in t and "price_val / _mp_check" not in t
    assert "price_val < TRADE_PRICE_SANITY_FLOOR" not in t
    assert t.count("trade_entry_checks.price_sanity(") == 2
    assert "trade_entry_checks.price_sanity_override_label()" in t
    assert "±50%" not in t
    assert '_tj_san["kind"] == "floor" and not _override_price' in t
    assert '_tj_san["kind"] == "ratio" and not _override_price' in t


def test_price_and_broker_findings_are_staged_after_time_and_replay_and_before_the_dup_check():
    t = _app_text()
    seg = _slice(t, "trade_entry_checks.time_findings(", 'st.session_state["_tj_pending_buy"] = record')
    for needle in ("sell_replay_finding(", "_tj_price_broker_stage(",
                   '_entry_f.extend(_entry_pb["findings"])',
                   'record["broker_txn_id"] = _tj_match_link["txn_id"]',
                   'broker_path=_is_broker_trade or bool(record.get("broker_txn_id"))'):
        assert needle in seg, needle
    assert (seg.index("sell_replay_finding(") < seg.index("_tj_price_broker_stage(")
            < seg.index("trade_confirm.find_duplicate_entries("))
    # manual entries only: the call sits after the broker branch of the staging block
    assert t.count("_tj_price_broker_stage(") == 2           # the def and the one call
    i = t.index("_entry_pb = _tj_price_broker_stage(")
    assert t.rindex("else:\n                    _tj_ft =", 0, i) > t.rindex("if _is_broker_trade:", 0, i)


def test_stage_helper_reads_price_and_broker_through_the_pure_checks_only():
    t = _app_text()
    body = _slice(t, "def _tj_price_broker_stage(", "# ── SELL confirmation card")
    for needle in ("price_check_needs_bar(", "bar_for_price_check(", "price_band_finding(",
                   "price_band_entry_finding(", "price_check_failed_finding()",
                   "load_snaptrade_config()", "unlinked_fills(", "fills_since(",
                   "load_snaptrade_pending_fills_or_none(", "pending_fill_findings(",
                   "broker_check_coverage(", "broker_coverage_finding(", "broker_unchecked_finding()",
                   "load_snaptrade_txn_synced_at_or_none()", "pending_id_for_txn("):
        assert needle in body, needle
    # three guards in all: one per half, plus the history fetch's own
    assert body.count("try:") == 3 and body.count("except Exception:") == 3
    # coverage only qualifies an EMPTY result (a real finding is always shown as it is)
    assert "if not _bk_f and not" in body and body.index("if not _bk_f and not") < body.index("broker_coverage_finding(")
    assert "unchecked_reason=_bar_why" in body and "now_et=_now_et()" in body


def test_matched_manual_entry_flips_its_pending_import_and_stale_ids_are_cleared():
    t = _app_text()
    start = 'if _is_broker_trade:\n                        st.session_state["_tj_pending_broker_id"]'
    seg = _slice(t, start, "st.rerun()")
    assert 'elif _tj_match_link is not None and _tj_match_link["pending_id"] is not None:' in seg
    assert '"pending_id": _tj_match_link["pending_id"]' in seg
    assert '"txn_id": _tj_match_link["txn_id"]' in seg
    assert 'st.session_state.pop("_tj_pending_broker_id", None)' in seg
    # both Cancel buttons and the broker "Not now" escape still drop the id
    for key in ('key="_tj_cancel_sell"', 'key="_tj_cancel_buy"'):
        blk = t[t.index(key):t.index("st.rerun()", t.index(key))]
        assert 'st.session_state.pop("_tj_pending_broker_id", None)' in blk, key
    abandon = t[t.index('key="_tj_broker_abandon"'):]
    assert 'st.session_state.pop("_tj_pending_broker_id", None)' in abandon[:300]
    # the confirm handlers consume it (pop, then flip the pending import)
    assert "mark_snaptrade_pending_import_logged(_ps_broker_pid)" in t
    assert "mark_snaptrade_pending_import_logged(_pb_broker_pid)" in t


def test_the_match_link_never_reaches_the_record_literal_or_save_trade():
    t = _app_text()
    assert "_tj_match_link" not in _slice(t, "record = {", "# SnapTrade dedup key")
    tree = parse_file_cached(_APP)
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "save_trade":
            assert "_tj_match_link" not in ast.unparse(n)
