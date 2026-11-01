"""Tests for stock_analyzer/llm_role_eval.py (chunk C2, llm-model-routing section 17.3).

No network, no DB: the production call functions are injected fakes.
"""
import ast
import json
import math
import random
from pathlib import Path

import pytest

from stock_analyzer import constants as C
from stock_analyzer import db, llm, news_intelligence
from stock_analyzer import llm_role_eval as ev
from stock_analyzer import trigger_price_check

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "premortem_trigger_cases.json"
EPS = 1e-6

CUR = llm.RoleSetting(model="cur-model", effort=None, capabilities={"efforts": ["low"]})
CAND = llm.RoleSetting(model="cand-model", effort="low", capabilities={"efforts": ["low", "high"]})


# --- capabilities_hash ----------------------------------------------------------------
def test_capabilities_hash_stable_and_order_independent():
    a = {"efforts": ["low", "high"], "thinking_types": [], "structured_outputs": True,
         "max_tokens": 64000, "max_input_tokens": 200000}
    b = {"max_input_tokens": 200000, "max_tokens": 64000, "structured_outputs": True,
         "thinking_types": [], "efforts": ["high", "low"]}
    assert ev.capabilities_hash(a) == ev.capabilities_hash(b)
    assert ev.capabilities_hash(a) == ev.capabilities_hash(dict(a))
    assert len(ev.capabilities_hash(a)) == 64
    assert ev.capabilities_hash(a) != ev.capabilities_hash({**a, "max_tokens": 1})


def test_capabilities_hash_none_safe_and_normalizes_objects():
    assert ev.capabilities_hash(None) == ev.capabilities_hash(None)
    assert ev.capabilities_hash(None) != ev.capabilities_hash({})

    class Info:   # a ModelInfo-like object with nothing set normalizes to a plain dict
        pass
    assert ev.capabilities_hash(Info()) == ev.capabilities_hash(llm.normalize_capabilities(Info()))


# --- pass functions at the boundary -----------------------------------------------------
def _score_metrics(**over):
    m = {"sample_n": C.LLM_EVAL_SCORE_MIN_TICKER_DAYS, "history_rows": 10,
         "mean_shift_net_abs": 0.0, "mean_abs_net": 0.0, "band_cross_net_pct": 0.0,
         "buy_entry_cross_net_pct": 0.0, "self_spread": 0.0,
         "cumulative_shift_abs": 0.0, "failure_pct": 0.0}
    m.update(over)
    return m


def _filter_metrics(**over):
    m = {"sample_n": C.LLM_EVAL_FILTER_MIN_HEADLINES, "loosen_net_pct": 0.0,
         "critical_release_net_pct": 0.0, "failure_pct": 0.0}
    m.update(over)
    return m


def _trigger_metrics(**over):
    m = {"fixture_n": 30, "invented": 0, "wrong_direction": 0, "fixture_wrong_price": 0,
         "fixture_misses": 0, "real_other": 0, "failure_pct": 0.0}
    m.update(over)
    return m


def test_score_all_zero_passes_and_min_sample_boundary():
    assert ev.score_failures(_score_metrics()) == []
    f = ev.score_failures(_score_metrics(sample_n=C.LLM_EVAL_SCORE_MIN_TICKER_DAYS - 1))
    assert len(f) == 1 and "LLM_EVAL_SCORE_MIN_TICKER_DAYS" in f[0]


@pytest.mark.parametrize("key,const", [(k, c) for k, c, _ in ev.SCORE_CHECKS])
def test_score_limit_equality_passes_epsilon_fails(key, const):
    limit = getattr(C, const)
    assert ev.score_failures(_score_metrics(**{key: float(limit)})) == []
    f = ev.score_failures(_score_metrics(**{key: float(limit) + EPS}))
    assert len(f) == 1 and const in f[0] and str(limit) in f[0]


def test_score_without_history_rows_fails():
    f = ev.score_failures(_score_metrics(history_rows=0))
    assert any("score_history" in line for line in f)


@pytest.mark.parametrize("key,const", [(k, c) for k, c, _ in ev.FILTER_CHECKS])
def test_filter_limit_equality_passes_epsilon_fails(key, const):
    limit = getattr(C, const)
    assert ev.filter_failures(_filter_metrics(**{key: float(limit)})) == []
    f = ev.filter_failures(_filter_metrics(**{key: float(limit) + EPS}))
    assert len(f) == 1 and const in f[0]


def test_filter_min_sample():
    f = ev.filter_failures(_filter_metrics(sample_n=C.LLM_EVAL_FILTER_MIN_HEADLINES - 1))
    assert len(f) == 1 and "LLM_EVAL_FILTER_MIN_HEADLINES" in f[0]


def test_filter_min_sample_counts_compared_not_sampled():
    # enough sampled, but exclusions leave too few compared: no verdict
    f = ev.filter_failures(_filter_metrics(sample_n=C.LLM_EVAL_FILTER_MIN_HEADLINES + 50,
                                           compared_n=C.LLM_EVAL_FILTER_MIN_HEADLINES - 1))
    assert len(f) == 1 and "LLM_EVAL_FILTER_MIN_HEADLINES" in f[0]
    assert ev.filter_failures(_filter_metrics(compared_n=C.LLM_EVAL_FILTER_MIN_HEADLINES)) == []


def test_filter_tier_pct_uses_compared_denominator_and_failures_use_sampled():
    # 4 negatives; the candidate's batch fails twice for item 3 only via a None score,
    # and loosens item 0: 1 loosened of 3 compared = 33.3%, failure 1 of 4 = 25%.
    items = [{"title": f"Company plunges on fraud probe, terrible losses {i}"} for i in range(4)]
    cur = llm.RoleSetting(model="cur-model")

    def batch(its, setting):
        model = setting[llm.ROLE_FILTER].model
        out = []
        for it in its:
            idx = int(it["title"].rsplit(" ", 1)[1])
            if model == "cand-model" and idx == 3:
                out.append(None)
            elif model == "cand-model" and idx == 0:
                out.append(0.9)
            else:
                out.append(-0.9)
        return out
    m = ev.eval_filter(items, batch, current=cur, candidate=CAND).metrics
    assert m["compared_n"] == 3 and m["excluded_n"] == 1
    assert m["loosen_candidate_pct"] == pytest.approx(100.0 / 3)
    assert m["failure_pct"] == pytest.approx(25.0)


@pytest.mark.parametrize("key,const", [(k, c) for k, c, _ in ev.TRIGGER_CHECKS])
def test_trigger_limit_equality_passes_step_over_fails(key, const):
    limit = getattr(C, const)
    step = 1 if isinstance(limit, int) and key != "failure_pct" else EPS
    assert ev.trigger_failures(_trigger_metrics(**{key: limit})) == []
    f = ev.trigger_failures(_trigger_metrics(**{key: limit + step}))
    assert len(f) == 1 and const in f[0]


def test_trigger_empty_fixture_fails():
    assert ev.trigger_failures(_trigger_metrics(fixture_n=0))


def test_unmeasurable_metric_fails_closed():
    f = ev.score_failures(_score_metrics(self_spread=None))
    assert len(f) == 1 and "LLM_EVAL_SCORE_MAX_SELF_SPREAD_PTS" in f[0]


# --- Δcomposite ----------------------------------------------------------------------------
def test_delta_composite_reads_the_weight_from_constants(monkeypatch):
    assert ev.delta_composite(0.1, 0.0) == pytest.approx(C.COMPOSITE_WEIGHTS["sentiment"] * 5.0)
    monkeypatch.setitem(C.COMPOSITE_WEIGHTS, "sentiment", 0.2)
    assert ev.delta_composite(0.1, 0.0) == pytest.approx(0.2 * 5.0)


def test_delta_composite_bound_within_max_swing():
    rng = random.Random(7)
    bound = C.COMPOSITE_WEIGHTS["sentiment"] * 100.0 * C.SENTIMENT_LLM_MAX_SWING * 2 / 2
    assert bound == pytest.approx(5.0)
    for _ in range(2000):
        n = rng.randint(1, 10)
        vader = [rng.uniform(-1, 1) for _ in range(n)]
        def out():
            return [max(-1.0, min(1.0, v + rng.uniform(-C.SENTIMENT_LLM_MAX_SWING,
                                                       C.SENTIMENT_LLM_MAX_SWING)))
                    for v in vader]
        a, b = sum(out()) / n, sum(out()) / n
        assert abs(ev.delta_composite(a, b)) <= 5.0 + 1e-9


# --- eval_score -------------------------------------------------------------------------------
HEADS = [{"headline": "Company reports quarterly results", "url": "u1"},
         {"headline": "Analysts discuss the outlook", "url": "u2"}]


def _vader_avg():
    hl = ev.vader_headlines(HEADS)
    return sum(h["score"] for h in hl) / len(hl), hl


def _rescore_by_model(shifts, none_for=()):
    """Fake production rescore: each model adds its fixed shift to every VADER score."""
    calls = []

    def rescore(headlines, ticker, setting):
        model = setting[llm.ROLE_SCORE].model
        calls.append(model)
        if model in none_for:
            return None
        out = []
        for h in headlines:
            d = dict(h)
            d["score"] = round(h["score"] + shifts[model], 3)
            out.append(d)
        return out
    rescore.calls = calls
    return rescore


def _row(ticker, date, avg_shift=0.0):
    avg, _ = _vader_avg()
    return {"ticker": ticker, "score_date": date, "headlines": HEADS,
            "avg_sent": round(avg + avg_shift, 3)}


def test_vader_headlines_match_production_shape_and_scores():
    from stock_analyzer.sentiment import analyze_news
    hl = ev.vader_headlines(HEADS)
    assert [h["headline"] for h in hl] == [h["headline"] for h in HEADS]
    assert set(hl[0]) == {"headline", "score", "label", "url"}
    assert hl[0]["score"] == analyze_news([{"title": HEADS[0]["headline"]}])[1][0]["score"]


def test_eval_score_net_of_noise_arithmetic_and_runs():
    rescore = _rescore_by_model({"cur-model": 0.02, "cand-model": 0.10})
    rows = [_row("AAA", "2026-10-01"), _row("BBB", "2026-10-01")]
    res = ev.eval_score(rows, {}, rescore, current=CUR, candidate=CAND)
    m = res.metrics
    # per row: candidate twice, current once
    assert rescore.calls.count("cand-model") == 4 and rescore.calls.count("cur-model") == 2
    assert m["sample_n"] == res.sample_n == 2
    w = C.COMPOSITE_WEIGHTS["sentiment"]
    assert m["mean_shift_candidate"] == pytest.approx(w * 50 * 0.10, abs=1e-3)
    assert m["mean_shift_current"] == pytest.approx(w * 50 * 0.02, abs=1e-3)
    assert m["mean_shift_net"] == pytest.approx(w * 50 * 0.08, abs=1e-3)
    assert m["mean_shift_net_abs"] == pytest.approx(abs(m["mean_shift_net"]))
    assert m["mean_abs_net"] == pytest.approx(m["mean_abs_candidate"] - m["mean_abs_current"])
    assert m["self_spread"] == pytest.approx(0.0)
    assert m["failure_pct"] == 0.0 and m["history_rows"] == 0
    assert res.role == "score" and res.model == "cand-model" and res.effort == "low"
    assert res.criteria_version == C.LLM_EVAL_CRITERIA_VERSION
    assert res.capabilities_hash == ev.capabilities_hash(CAND.capabilities)
    assert res.passed is False and res.failures   # sample far below the minimum


def test_eval_score_baseline_is_the_stored_row():
    # stored avg already 0.10 above VADER: the candidate (+0.10) matches it, the current doesn't
    rescore = _rescore_by_model({"cur-model": 0.0, "cand-model": 0.10})
    res = ev.eval_score([_row("AAA", "2026-10-01", avg_shift=0.10)], {}, rescore,
                        current=CUR, candidate=CAND)
    assert res.metrics["mean_shift_candidate"] == pytest.approx(0.0, abs=1e-3)
    assert res.metrics["mean_shift_current"] == pytest.approx(-0.5, abs=1e-3)


def test_eval_score_cumulative_adds_the_net_signed_mean():
    rescore = _rescore_by_model({"cur-model": 0.0, "cand-model": 0.10})
    res = ev.eval_score([_row("AAA", "2026-10-01")], {}, rescore, current=CUR,
                        candidate=CAND, cumulative_shift_pts=-0.4)
    net = res.metrics["mean_shift_net"]
    assert net == pytest.approx(0.5, abs=1e-3)
    assert res.metrics["cumulative_shift_abs"] == pytest.approx(abs(-0.4 + net))
    assert res.metrics["cumulative_prior_pts"] == -0.4


def test_eval_score_band_crossings_including_up_into_buy():
    rescore = _rescore_by_model({"cur-model": 0.02, "cand-model": 0.10})
    rows = [_row("AAA", "2026-10-01"), _row("BBB", "2026-10-01")]
    hist = {("AAA", "2026-10-01"): 64.8,    # +0.5 -> 65.3 crosses up into Buy; +0.1 -> 64.9 stays Hold
            ("BBB", "2026-10-01"): 70.0}    # stays Buy either way
    m = ev.eval_score(rows, hist, rescore, current=CUR, candidate=CAND).metrics
    assert m["history_rows"] == 2
    assert m["band_cross_candidate_pct"] == pytest.approx(50.0)
    assert m["band_cross_current_pct"] == 0.0
    assert m["band_cross_net_pct"] == pytest.approx(50.0)
    assert m["buy_entry_cross_candidate_pct"] == pytest.approx(50.0)
    assert m["buy_entry_cross_net_pct"] == pytest.approx(50.0)


def test_eval_score_downward_band_cross_is_not_a_buy_entry():
    rescore = _rescore_by_model({"cur-model": 0.0, "cand-model": -0.10})
    hist = {("AAA", "2026-10-01"): 65.2}    # -0.5 -> 64.7: Buy -> Hold
    m = ev.eval_score([_row("AAA", "2026-10-01")], hist, rescore,
                      current=CUR, candidate=CAND).metrics
    assert m["band_cross_candidate_pct"] == pytest.approx(100.0)
    assert m["buy_entry_cross_candidate_pct"] == 0.0


def test_eval_score_failures_counted_per_candidate_call():
    rescore = _rescore_by_model({"cur-model": 0.0, "cand-model": 0.0}, none_for=("cand-model",))
    m = ev.eval_score([_row("AAA", "2026-10-01")], {}, rescore, current=CUR,
                      candidate=CAND).metrics
    assert m["candidate_calls"] == 2 and m["candidate_failures"] == 2
    assert m["failure_pct"] == 100.0 and m["sample_n"] == 0


def test_eval_score_none_raising_or_non_numeric_calls_are_failures():
    # (production's rescore_headlines_llm always returns a full-length copy, so an
    # incomplete array is not a shape this eval can see)
    def rescore(headlines, ticker, setting):
        if setting[llm.ROLE_SCORE].model == "cand-model":
            raise RuntimeError("boom")
        return None
    m = ev.eval_score([_row("AAA", "2026-10-01")], {}, rescore, current=CUR,
                      candidate=CAND).metrics
    assert m["failure_pct"] == 100.0 and m["current_failures"] == 1

    def non_numeric(headlines, ticker, setting):
        return [dict(h, score="n/a") for h in headlines]
    m2 = ev.eval_score([_row("AAA", "2026-10-01")], {}, non_numeric, current=CUR,
                       candidate=CAND).metrics
    assert m2["failure_pct"] == 100.0 and m2["current_failures"] == 1


def test_eval_score_reports_mean_abs_candidate_vs_current():
    def rescore(headlines, ticker, setting):
        shift = 0.10 if setting[llm.ROLE_SCORE].model == "cand-model" else 0.0
        return [dict(h, score=round(h["score"] + shift, 3)) for h in headlines]
    m = ev.eval_score([_row("AAA", "2026-10-01")], {}, rescore, current=CUR,
                      candidate=CAND).metrics
    assert m["mean_abs_candidate_vs_current"] == pytest.approx(
        C.COMPOSITE_WEIGHTS["sentiment"] * 50 * 0.10, abs=1e-3)


def test_eval_score_self_spread_between_runs():
    state = {"n": 0}

    def rescore(headlines, ticker, setting):
        model = setting[llm.ROLE_SCORE].model
        shift = 0.0
        if model == "cand-model":
            state["n"] += 1
            shift = 0.10 if state["n"] % 2 else 0.0   # run A +0.1, run B 0.0
        return [dict(h, score=round(h["score"] + shift, 3)) for h in headlines]
    m = ev.eval_score([_row("AAA", "2026-10-01")], {}, rescore, current=CUR,
                      candidate=CAND).metrics
    assert m["self_spread"] == pytest.approx(C.COMPOSITE_WEIGHTS["sentiment"] * 50 * 0.10, abs=1e-3)


def test_eval_score_can_pass_with_enough_identical_rows():
    rescore = _rescore_by_model({"cur-model": 0.0, "cand-model": 0.0})
    rows = [_row(f"T{i}", "2026-10-01") for i in range(C.LLM_EVAL_SCORE_MIN_TICKER_DAYS)]
    hist = {(r["ticker"], "2026-10-01"): 50.0 for r in rows}
    res = ev.eval_score(rows, hist, rescore, current=CUR, candidate=CAND)
    assert res.sample_n == C.LLM_EVAL_SCORE_MIN_TICKER_DAYS
    assert res.passed and res.failures == []
    one_short = ev.eval_score(rows[:-1], hist, rescore, current=CUR, candidate=CAND)
    assert not one_short.passed


def test_eval_score_skips_rows_without_headlines_or_avg():
    rescore = _rescore_by_model({"cur-model": 0.0, "cand-model": 0.0})
    rows = [{"ticker": "X", "score_date": "d", "headlines": [], "avg_sent": 0.0},
            {"ticker": "Y", "score_date": "d", "headlines": HEADS, "avg_sent": None}]
    res = ev.eval_score(rows, {}, rescore, current=CUR, candidate=CAND)
    assert res.sample_n == 0 and rescore.calls == []


# --- eval_filter ----------------------------------------------------------------------------------
def _neg_titles(n):
    return [{"ticker": f"T{i % 7}", "title": f"Company {i} faces fraud scandal, lawsuit and bankruptcy fears"}
            for i in range(n)]


def test_filter_uses_production_suppress_only():
    assert ev._apply_suppress_only is news_intelligence._apply_suppress_only
    assert ev._RESCORE_BATCH == news_intelligence._RESCORE_BATCH == 25


def test_select_negative_headlines_filters_dedupes_and_shapes_items():
    heads = _neg_titles(3) + [{"ticker": "T0", "title": "Company 0 faces fraud scandal, lawsuit and bankruptcy fears"},
                              {"ticker": "Z", "title": "Wonderful great record profits and success"}]
    items = ev.select_negative_headlines(heads)
    assert len(items) == 3
    assert all(i["compound"] <= C.NEWS_SENTIMENT_WARN for i in items)
    assert set(items[0]) == {"ticker", "title", "compound", "label"}


def _filter_fakes(n, release_score=None, noise_titles=()):
    state = {"cur": 0, "sizes": []}
    nb = math.ceil(n / 25)

    def score_batch(items, setting):
        model = setting[llm.ROLE_FILTER].model
        state["sizes"].append((model, len(items)))
        if model == "cur-model":
            state["cur"] += 1
            second_pass = (state["cur"] - 1) // nb == 1
            return [0.5 if (second_pass and i["title"] in noise_titles) else i["compound"]
                    for i in items]
        return [release_score if release_score is not None else i["compound"] for i in items]
    return score_batch, state


def test_filter_batches_like_production_and_runs_current_twice_candidate_once():
    n = 60
    sb, state = _filter_fakes(n)
    res = ev.eval_filter(_neg_titles(n), sb, current=CUR, candidate=CAND)
    assert res.sample_n == n
    assert max(sz for _, sz in state["sizes"]) == 25
    cur_calls = [s for m, s in state["sizes"] if m == "cur-model"]
    cand_calls = [s for m, s in state["sizes"] if m == "cand-model"]
    assert cur_calls == [25, 25, 10] * 2 and cand_calls == [25, 25, 10]


def test_filter_loosening_and_critical_release_net_of_noise():
    n = 40
    heads = _neg_titles(n)
    items = ev.select_negative_headlines(heads)
    assert all(ev.news_tier(i["compound"]) == ev.TIER_CRITICAL for i in items)   # sanity
    noise = {items[0]["title"], items[1]["title"]}                              # 2 of 40 = 5%
    sb, _ = _filter_fakes(n, release_score=0.5, noise_titles=noise)
    m = ev.eval_filter(heads, sb, current=CUR, candidate=CAND).metrics
    assert m["loosen_candidate_pct"] == pytest.approx(100.0)
    assert m["loosen_noise_pct"] == pytest.approx(5.0)
    assert m["loosen_net_pct"] == pytest.approx(95.0)
    assert m["critical_release_net_pct"] == pytest.approx(95.0)
    assert m["tighten_candidate_pct"] == 0.0


def test_filter_release_line_is_strictly_above_the_warn_threshold():
    heads = _neg_titles(2)
    at_warn, _ = _filter_fakes(2, release_score=C.NEWS_SENTIMENT_WARN)
    m = ev.eval_filter(heads, at_warn, current=CUR, candidate=CAND).metrics
    assert m["loosen_candidate_pct"] == 100.0           # critical -> warn is less severe
    assert m["critical_release_candidate_pct"] == 0.0   # but not above the warn line
    above, _ = _filter_fakes(2, release_score=C.NEWS_SENTIMENT_WARN + 0.01)
    m2 = ev.eval_filter(heads, above, current=CUR, candidate=CAND).metrics
    assert m2["critical_release_candidate_pct"] == 100.0


def test_filter_candidate_cannot_tighten_below_vader_and_failures_leave_vader():
    n = 30
    lower, _ = _filter_fakes(n, release_score=-1.0)     # below VADER: suppress-only ignores it
    m = ev.eval_filter(_neg_titles(n), lower, current=CUR, candidate=CAND).metrics
    assert m["tighten_candidate_pct"] == 0.0 and m["loosen_candidate_pct"] == 0.0

    def failing(items, setting):
        return None if setting[llm.ROLE_FILTER].model == "cand-model" else [i["compound"] for i in items]
    m2 = ev.eval_filter(_neg_titles(n), failing, current=CUR, candidate=CAND).metrics
    assert m2["failure_pct"] == 100.0 and m2["loosen_candidate_pct"] == 0.0


def test_filter_transient_batch_failure_succeeds_on_retry_and_counts_nothing():
    n = 10
    calls = {"n": 0}

    def per_run_flaky(items, setting):
        calls["n"] += 1
        if calls["n"] % 2 == 1:         # every first attempt fails, the retry succeeds
            raise RuntimeError("blip")
        return [i["compound"] for i in items]
    m = ev.eval_filter(_neg_titles(n), per_run_flaky, current=CUR, candidate=CAND).metrics
    assert m["failure_pct"] == 0.0 and m["excluded_n"] == 0
    assert m["failed_items_c1"] == m["failed_items_c2"] == m["failed_items_candidate"] == 0
    assert calls["n"] == 6              # 3 runs x (fail + retry)


def test_filter_batch_failing_twice_counts_all_its_items_and_excludes_them():
    n = 30                              # batches of 25 + 5
    heads = _neg_titles(n)
    items = ev.select_negative_headlines(heads)
    last5 = {i["title"] for i in items[25:]}

    def bad_tail_for_candidate(batch, setting):
        if setting[llm.ROLE_FILTER].model == "cand-model" and batch[0]["title"] in last5:
            raise RuntimeError("down")
        return [i["compound"] for i in batch]
    m = ev.eval_filter(heads, bad_tail_for_candidate, current=CUR, candidate=CAND).metrics
    assert m["failed_items_candidate"] == 5 and m["failed_items_c1"] == 0
    assert m["failure_pct_candidate"] == pytest.approx(500.0 / 30)
    assert m["failure_pct"] == m["failure_pct_candidate"]
    assert m["excluded_n"] == 5 and m["compared_n"] == 25
    assert m["critical_n"] == 25        # critical under C1, among compared items


def test_filter_incomplete_batch_counts_as_failure():
    def short(items, setting):
        return [i["compound"] for i in items][:-1]
    m = ev.eval_filter(_neg_titles(10), short, current=CUR, candidate=CAND).metrics
    assert m["failure_pct"] == 100.0


def test_filter_pass_needs_the_minimum_sample():
    n = C.LLM_EVAL_FILTER_MIN_HEADLINES
    sb, _ = _filter_fakes(n)
    assert ev.eval_filter(_neg_titles(n), sb, current=CUR, candidate=CAND).passed
    sb2, _ = _filter_fakes(n - 1)
    assert not ev.eval_filter(_neg_titles(n - 1), sb2, current=CUR, candidate=CAND).passed


# --- extract_trigger -----------------------------------------------------------------------------
def _ok(price, direction="below"):
    return {"checkable": True, "direction": direction, "price_level": price}


NOT = {"checkable": False, "direction": None, "price_level": None}


def test_classify_trigger_each_class():
    t = "I'm wrong if it falls below $150."
    cls = ev.classify_trigger
    assert cls(t, 150.0, "below", _ok(150.0)) == ev.CLS_OK
    assert cls(t, 150.0, "below", _ok(150.005)) == ev.CLS_OK                 # within the tolerance
    assert cls(t, 150.0, "below", _ok(150.0 + C.LLM_EVAL_TRIGGER_PRICE_TOL + 0.001)) in (
        ev.CLS_WRONG_PRICE, ev.CLS_INVENTED)
    assert cls(t, 150.0, "below", _ok(150.0, "above")) == ev.CLS_WRONG_DIRECTION
    assert cls(t, 150.0, "below", NOT) == ev.CLS_MISSED
    assert cls(t, 150.0, "below", None) == ev.CLS_PARSE_FAILURE
    assert cls(t, 150.0, "below", {"checkable": True, "direction": "sideways", "price_level": 1}) \
        == ev.CLS_PARSE_FAILURE
    # invented: nothing expected but a price came back
    assert cls("Stop at -8%.", None, None, _ok(8.0)) == ev.CLS_INVENTED
    # invented: a price that appears nowhere in the text
    assert cls(t, 150.0, "below", _ok(140.0)) == ev.CLS_INVENTED
    assert cls("Out below $1,234.56", 1234.56, "below", _ok(1234.56)) == ev.CLS_OK
    # nothing expected and nothing extracted is a pass
    assert cls("qualitative only", None, None, NOT) == ev.CLS_OK


def test_classify_real_trade_price_in_text_with_no_stored_trigger_is_a_disagreement():
    # The stored trigger is the old model's output, not ground truth: a price that IS
    # in the text is a listed disagreement on a real trade, but still "invented" on a
    # fixture case (whose label is ground truth).
    t = "Out if it closes under $90."
    assert ev.classify_trigger(t, None, None, _ok(90.0), expected_is_truth=False) == ev.CLS_NEW_PRICE
    assert ev.classify_trigger(t, None, None, _ok(90.0)) == ev.CLS_INVENTED
    # a price that appears nowhere in the text is invented either way
    assert ev.classify_trigger(t, None, None, _ok(85.0), expected_is_truth=False) == ev.CLS_INVENTED


def test_eval_trigger_real_new_price_counts_as_other_not_invented():
    # "not_checkable" = the old model stored "no price": an in-text price is NEW_PRICE
    real = [{"id": 21, "premortem_commitment": "Out if it closes under $90.",
             "premortem_trigger_price": None, "premortem_trigger_direction": "not_checkable"}]
    m = ev.eval_trigger([], real, lambda text, setting: _ok(90.0), candidate=CAND).metrics
    assert m["invented"] == 0 and m["real_other"] == 2
    assert [d["class"] for d in m["real_disagreements"]] == [ev.CLS_NEW_PRICE] * 2
    assert not any(d.get("unlabelled") for d in m["real_disagreements"])
    # a not_checkable row with a price that is NOT in the text is invented
    m2 = ev.eval_trigger([], real, lambda text, setting: _ok(85.0), candidate=CAND).metrics
    assert m2["invented"] == 2 and m2["real_other"] == 0


def test_eval_trigger_null_direction_row_is_unlabelled():
    real = [{"id": 31, "premortem_commitment": "Out if it closes under $90.",
             "premortem_trigger_price": None, "premortem_trigger_direction": None}]
    m = ev.eval_trigger([], real, lambda text, setting: _ok(90.0), candidate=CAND).metrics
    assert m["real_other"] == 0 and m["invented"] == 0 and m["wrong_direction"] == 0
    assert m["fixture_misses"] == 0 and m["misses"] == 0
    assert [d["class"] for d in m["real_disagreements"]] == [ev.CLS_NEW_PRICE] * 2
    assert all(d["unlabelled"] is True for d in m["real_disagreements"])
    # the engine check: the same model extracting the same price for a NULL row passes
    assert ev.eval_trigger([{"id": "f", "text": "Wrong below $150.",
                             "expected": {"price": 150.0, "direction": "below"}}],
                           real, lambda t, s: _ok(90.0 if "$90" in t else 150.0),
                           candidate=CAND).passed
    # a price that appears nowhere in the text is invented, even when unlabelled
    m2 = ev.eval_trigger([], real, lambda text, setting: _ok(85.0), candidate=CAND).metrics
    assert m2["invented"] == 2 and m2["real_other"] == 0
    assert all(d["unlabelled"] for d in m2["real_disagreements"])
    # a parse failure counts
    m3 = ev.eval_trigger([], real, lambda text, setting: None, candidate=CAND).metrics
    assert m3["parse_failures"] == 2 and m3["failure_pct"] == 100.0


def test_classify_wrong_price_when_text_has_two_numbers():
    t = "Bought at $155; I'm out under $140."
    assert ev.classify_trigger(t, 140.0, "below", _ok(155.0)) == ev.CLS_WRONG_PRICE


def test_eval_trigger_counts_both_runs_and_lists_real_disagreements():
    fixture = [
        {"id": "f1", "text": "Wrong below $150.", "expected": {"price": 150.0, "direction": "below"}},
        {"id": "f2", "text": "Stop at -8%.", "expected": {"price": None, "direction": None}},
    ]
    real = [{"id": 11, "premortem_commitment": "Out under $90.",
             "premortem_trigger_price": 90.0, "premortem_trigger_direction": "below"},
            {"id": 12, "premortem_commitment": "Earnings miss would be it.",
             "premortem_trigger_price": None, "premortem_trigger_direction": None}]
    seen = []

    def extract(text, setting):
        seen.append(setting[llm.ROLE_EXTRACT_TRIGGER].model)
        if text.startswith("Wrong below"):
            return _ok(150.0)
        if text.startswith("Stop"):
            return NOT
        if text.startswith("Out under"):
            return NOT            # a miss on a real row: "other" disagreement, both runs
        return NOT
    res = ev.eval_trigger(fixture, real, extract, candidate=CAND)
    m = res.metrics
    assert seen == ["cand-model"] * 8                       # 4 cases x 2 runs
    assert m["sample_n"] == 4 and m["fixture_n"] == 2 and m["real_n"] == 2
    assert m["invented"] == 0 and m["wrong_direction"] == 0 and m["fixture_misses"] == 0
    assert m["real_other"] == 2                              # trade 11 missed on both runs
    assert [d["trade_id"] for d in m["real_disagreements"]] == [11, 11]
    assert m["real_disagreements"][0]["stored"] == {"price": 90.0, "direction": "below"}
    assert res.passed is False and any("LLM_EVAL_TRIGGER_MAX_REAL_DISAGREE" in f for f in res.failures)
    assert res.baseline == ev.BASELINE_TRIGGER and res.role == "extract_trigger"


def test_eval_trigger_invented_wrong_direction_and_failures_fail():
    fixture = [{"id": "f1", "text": "Wrong below $150.", "expected": {"price": 150.0, "direction": "below"}},
               {"id": "f2", "text": "Stop at -8%.", "expected": {"price": None, "direction": None}}]
    flip = {"n": 0}

    def extract(text, setting):
        if text.startswith("Wrong"):
            return _ok(150.0, "above")
        flip["n"] += 1
        return _ok(8.0) if flip["n"] == 1 else None
    m = ev.eval_trigger(fixture, [], extract, candidate=CAND).metrics
    assert m["wrong_direction"] == 2 and m["invented"] == 1 and m["parse_failures"] == 1
    assert m["failure_pct"] == pytest.approx(25.0)


def test_eval_trigger_miss_count_is_summed_over_both_runs_and_one_is_the_limit():
    fixture = [{"id": "f1", "text": "Wrong below $150.", "expected": {"price": 150.0, "direction": "below"}}]
    clean = ev.eval_trigger(fixture, [], lambda t, s: _ok(150.0), candidate=CAND)
    assert clean.passed and clean.failures == []
    # one miss in ONE run only: at the limit (LLM_EVAL_TRIGGER_MAX_MISSES = 1) passes
    runs = iter([NOT, _ok(150.0)])
    once = ev.eval_trigger(fixture, [], lambda t, s: next(runs), candidate=CAND)
    assert once.metrics["fixture_misses"] == 1 == C.LLM_EVAL_TRIGGER_MAX_MISSES and once.passed
    twice = ev.eval_trigger(fixture, [], lambda t, s: NOT, candidate=CAND)
    assert twice.metrics["fixture_misses"] == 2 and not twice.passed


# --- fixture ------------------------------------------------------------------------------------
def _fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_loads_with_required_keys():
    cases = _fixture()
    assert len(cases) >= 27
    assert len({c["id"] for c in cases}) == len(cases)
    for c in cases:
        assert set(c) >= {"id", "text", "expected", "note"}
        assert isinstance(c["text"], str) and c["text"].strip()
        assert c["note"].strip()
        exp = c["expected"]
        assert set(exp) == {"price", "direction"}
        assert exp["direction"] in ("above", "below", None)
        assert (exp["price"] is None) == (exp["direction"] is None)
        if exp["price"] is not None:
            assert isinstance(exp["price"], float) and exp["price"] > 0


def test_fixture_labelled_prices_appear_in_their_text_and_cover_the_classes():
    cases = _fixture()
    for c in cases:
        p = c["expected"]["price"]
        if p is not None:
            assert any(abs(n - p) <= C.LLM_EVAL_TRIGGER_PRICE_TOL for n in trigger_price_check.price_mentions(c["text"])), c["id"]
    dirs = {c["expected"]["direction"] for c in cases}
    assert dirs == {"above", "below", None}
    assert sum(c["expected"]["price"] is None for c in cases) >= 8


def test_a_perfect_extractor_passes_the_fixture():
    cases = _fixture()
    by_text = {c["text"]: c["expected"] for c in cases}

    def perfect(text, setting):
        e = by_text[text]
        return NOT if e["price"] is None else _ok(e["price"], e["direction"])
    res = ev.eval_trigger(cases, [], perfect, candidate=CAND)
    assert res.passed, res.failures


# --- db: legacy-select fallback ------------------------------------------------------------------
class _FakeQuery:
    def __init__(self, data, fail_on_cols):
        self._data, self._fail, self.cols = data, fail_on_cols, None

    def select(self, cols):
        self.cols = cols
        return self

    def __getattr__(self, name):
        if name in ("gte", "lte", "order", "range", "eq", "limit"):
            return lambda *a, **k: self
        raise AttributeError(name)

    def execute(self):
        if "model" in (self.cols or "") and self._fail:
            raise Exception("{'code': '42703', 'message': 'column sentiment_llm_cache.model does not exist'}")
        return type("R", (), {"data": [dict(r) for r in self._data]})()


class _FakeClient:
    def __init__(self, data, fail_on_cols=True):
        self.q = _FakeQuery(data, fail_on_cols)
        self.tables = []

    def table(self, name):
        self.tables.append(name)
        return self.q


def test_cache_sample_legacy_select_fallback(monkeypatch, capsys):
    data = [{"ticker": "AAA", "score_date": "2026-10-01", "headlines": HEADS, "avg_sent": 0.1},
            {"ticker": "BAD", "score_date": "2026-10-01", "headlines": None, "avg_sent": 0.1}]
    fc = _FakeClient(data)
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: fc)
    rows = db.load_sentiment_cache_sample("2026-09-01", "2026-10-31")
    assert [r["ticker"] for r in rows] == ["AAA"]
    assert rows[0]["model"] is None and rows[0]["effort"] is None
    out = capsys.readouterr().out
    assert "first read failed" in out and "without model/effort columns" in out
    assert "columns unavailable" not in out
    assert fc.tables == ["sentiment_llm_cache"] * 2


def test_cache_sample_uses_provenance_columns_when_present(monkeypatch, capsys):
    data = [{"ticker": "AAA", "score_date": "2026-10-01", "headlines": HEADS, "avg_sent": 0.1,
             "model": "m", "effort": "low"}]
    fc = _FakeClient(data, fail_on_cols=False)
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: fc)
    rows = db.load_sentiment_cache_sample("a", "b")
    assert rows[0]["model"] == "m" and "legacy" not in capsys.readouterr().out


def test_db_readers_return_none_offline(monkeypatch):
    monkeypatch.setattr(db, "has_db", lambda: False)
    assert db.load_sentiment_cache_sample("a", "b") is None
    assert db.load_score_history_composites("a", "b") is None
    assert db.load_premortem_trigger_rows() is None
    assert db.load_latest_llm_role_eval("score", "m", None) is None
    assert db.save_llm_role_eval({"role": "score"}) is None


def test_score_history_composites_keyed_by_ticker_and_date(monkeypatch):
    rows = [{"ticker": "aaa", "score_date": "2026-10-01", "composite": "64.8"},
            {"ticker": "BBB", "score_date": "2026-10-01", "composite": None}]
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "_client", lambda: _FakeClient(rows, fail_on_cols=False))
    assert db.load_score_history_composites("a", "b") == {("AAA", "2026-10-01"): 64.8}


# --- AST pin: the eval only ever writes llm_role_evals ----------------------------------------------
_FORBIDDEN_NAMES = {"save_sentiment_llm_cache", "save_reference_table", "upsert"}
_FORBIDDEN_ATTRS = {"upsert", "insert", "update", "delete"}


def _identifiers(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names, attrs = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            # `sys.path.insert(...)` (the script's repo-root import line) is
            # not a DB write; every other receiver still counts.
            if (node.attr == "insert" and isinstance(node.value, ast.Attribute)
                    and node.value.attr == "path"
                    and isinstance(node.value.value, ast.Name)
                    and node.value.value.id == "sys"):
                continue
            attrs.add(node.attr)
            names.add(node.attr)
        elif isinstance(node, (ast.ImportFrom,)):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.alias):
            names.add(node.name.split(".")[-1])
    return names, attrs


@pytest.mark.parametrize("rel", ["stock_analyzer/llm_role_eval.py", "scripts/llm_role_eval.py"])
def test_eval_never_writes_cache_trades_or_reference_tables(rel):
    names, attrs = _identifiers(ROOT / rel)
    assert not (names & _FORBIDDEN_NAMES)
    assert not (attrs & _FORBIDDEN_ATTRS)
    import re
    trade_writers = [n for n in names if re.match(r"^(save|insert|update|delete|upsert|add|record|log)_.*trade", n)]
    assert trade_writers == []


def test_script_saves_only_through_save_llm_role_eval():
    names, _ = _identifiers(ROOT / "scripts" / "llm_role_eval.py")
    writers = {n for n in names if n.startswith(("save_", "write_"))}
    assert writers == {"save_llm_role_eval"}


# ---- scripts/llm_role_eval.score_sample (C3 review A2) ----
def _load_script():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "llm_role_eval_script", ROOT / "scripts" / "llm_role_eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_score_sample_uses_row_matches_null_rows_are_haiku_4_5(monkeypatch):
    from stock_analyzer import sentiment_cache
    script = _load_script()
    rows = [{"ticker": "A", "model": None, "effort": None},
            {"ticker": "B", "model": llm.HAIKU_4_5, "effort": None},
            {"ticker": "C", "model": "claude-haiku-5-5", "effort": None},
            {"ticker": "D", "model": "claude-haiku-5-5", "effort": "low"}]
    old = llm.RoleSetting(model=llm.HAIKU_4_5)
    new = llm.RoleSetting(model="claude-haiku-5-5")
    assert [r["ticker"] for r in script.score_sample(rows, old)] == ["A", "B"]
    # a NULL model does NOT match a Haiku 5.5 current entry
    assert [r["ticker"] for r in script.score_sample(rows, new)] == ["C"]
    seen = []
    real = sentiment_cache.row_matches
    monkeypatch.setattr(sentiment_cache, "row_matches",
                        lambda row, e: seen.append(row["ticker"]) or real(row, e))
    script.score_sample(rows, old)
    assert seen == ["A", "B", "C", "D"]


# ---- eval fixes after the first real runs (E1-E6) ----
@pytest.fixture(autouse=True)
def _no_retry_pause(monkeypatch):
    monkeypatch.setattr(ev, "RETRY_PAUSE_SEC", 0.0)


def test_e1_blank_commitment_rows_are_skipped_not_failed():
    real = [{"id": 1, "premortem_commitment": "   ", "premortem_trigger_price": None,
             "premortem_trigger_direction": None},
            {"id": 2, "premortem_commitment": None, "premortem_trigger_price": None,
             "premortem_trigger_direction": None},
            {"id": 3, "premortem_commitment": "Out under $90.", "premortem_trigger_price": 90.0,
             "premortem_trigger_direction": "below"}]
    calls = []

    def extract(text, setting):
        calls.append(text)
        return _ok(90.0)
    m = ev.eval_trigger([], real, extract, candidate=CAND).metrics
    assert m["real_blank_skipped"] == 2 and m["real_n"] == 1 and m["sample_n"] == 1
    assert calls == ["Out under $90."] * 2
    assert m["parse_failures"] == 0 and m["failure_pct"] == 0.0


def test_e2_fixture_outcomes_list_non_ok_cases_only():
    fixture = [{"id": "f1", "text": "Wrong below $150.", "expected": {"price": 150.0, "direction": "below"}},
               {"id": "f2", "text": "Stop at -8%.", "expected": {"price": None, "direction": None}}]
    m = ev.eval_trigger(fixture, [], lambda t, s: _ok(8.0) if t.startswith("Stop") else _ok(150.0),
                        candidate=CAND).metrics
    fo = m["fixture_outcomes"]
    assert [c["id"] for c in fo] == ["f2", "f2"]
    assert fo[0]["class"] == ev.CLS_INVENTED and fo[0]["text"] == "Stop at -8%."
    assert fo[0]["expected"] == {"price": None, "direction": None} and fo[0]["got"]["price_level"] == 8.0


def test_e4_score_transient_failure_retried_once_and_not_counted():
    state = {"n": 0}
    sleeps = []

    def rescore(headlines, ticker, setting):
        state["n"] += 1
        if state["n"] == 1:
            return None
        return [dict(h) for h in headlines]
    m = ev.eval_score([_row("AAA", "2026-10-01")], {}, rescore, current=CUR,
                      candidate=CAND, sleep=sleeps.append).metrics
    # default failure_kind (unknown): no retry, the failure counts as a model failure
    assert m["failure_pct"] == 50.0 and m["current_failures"] == 0
    assert m["retried_calls_candidate"] == 0 and sleeps == []


def test_e4_score_double_failure_counts():
    rescore = _rescore_by_model({"cur-model": 0.0, "cand-model": 0.0}, none_for=("cand-model",))
    m = ev.eval_score([_row("AAA", "2026-10-01")], {}, rescore, current=CUR,
                      candidate=CAND, sleep=lambda s: None).metrics
    assert m["candidate_failures"] == 2 and m["retried_calls_candidate"] == 0
    assert rescore.calls.count("cand-model") == 2      # unknown kind: not retried


def test_e4_filter_retry_uses_the_pause_constant(monkeypatch):
    monkeypatch.setattr(ev, "RETRY_PAUSE_SEC", 2.0)
    sleeps, state = [], {"n": 0}

    def sb(items, setting):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("boom")
        return [{"score": -0.9, "label": "negative"} for _ in items]
    ev._filter_run([{"ticker": "T", "title": "x", "compound": -0.9, "label": "negative"}],
                   sb, {llm.ROLE_FILTER: CUR}, 1, sleeps.append)
    assert sleeps == [2.0]


def _res(status=None, error=None, stop=None, text=None):
    return llm.LLMResult(text=text, stop_reason=stop, error=error, model="m", usage=None,
                         status_code=status)


def test_b1_classify_failure_kinds():
    cf = ev.classify_failure
    for st in (429, 500, 502, 503, 504, 529):
        assert cf(_res(st, "x")) == ev.KIND_API
    for st in (400, 401, 403, 404):                     # billing / auth / model refused
        assert cf(_res(st, "BadRequestError: credit balance is too low")) == ev.KIND_API_FATAL
    assert cf(_res(None, "APIConnectionError: Connection error.")) == ev.KIND_API
    assert cf(_res(None, "APITimeoutError: Request timed out.")) == ev.KIND_MODEL   # a timeout is "model"
    assert cf(_res(None, "unexpected stop_reason: refusal", stop="refusal")) == ev.KIND_MODEL
    assert cf(_res(None, "no text in reply (stop_reason=max_tokens)", stop="max_tokens")) == ev.KIND_MODEL
    assert cf(_res(None, "no text in reply (empty text, stop_reason=end_turn)", stop="end_turn")) == ev.KIND_MODEL
    assert cf(_res(text="ok", stop="end_turn")) == ev.KIND_MODEL    # parsed badly downstream
    assert cf(None) == ev.KIND_MODEL                                # no failed call recorded


def _score_with_results(results_by_call, **kw):
    """eval_score over one row where call N returns results_by_call[N] (an LLMResult
    for a failed call, None for a good reply); failures are recorded in a tally."""
    tally = ev.CallTally()
    state = {"n": 0}

    def rescore(headlines, ticker, setting):
        r = results_by_call(state["n"], setting[llm.ROLE_SCORE].model)
        state["n"] += 1
        if r is None:
            return [dict(h) for h in headlines]
        tally.record(r)
        return None
    sleeps = []
    res = ev.eval_score([_row("AAA", "2026-10-01")], {}, rescore, current=CUR, candidate=CAND,
                        sleep=sleeps.append, failure_kind=lambda: ev.classify_failure(tally.last()),
                        failure_info=lambda: ev.call_info(tally.last()), **kw)
    return res, sleeps, state


def test_b1_rate_limit_failures_recovered_by_the_single_retry():
    def calls(n, model):          # n = 0, 2, 4 are the first attempts: 429, then the retry is good
        return _res(429, "RateLimitError: slow down") if n % 2 == 0 else None
    res, sleeps, _ = _score_with_results(calls)
    m = res.metrics
    assert m["failure_pct"] == 0.0 and m["api_failures_unrecovered"] == 0 and m["model_failures"] == 0
    assert m["retried_calls_candidate"] == 2 and m["retried_calls_current"] == 1
    assert sleeps == [ev.RETRY_PAUSE_SEC] * 3
    assert ev.api_problem(m) is None


def test_b1_model_failures_are_never_retried_and_count():
    for r in (_res(None, "unexpected stop_reason: refusal", stop="refusal"),
              _res(None, "APITimeoutError: Request timed out."),
              _res(None, "no text in reply (stop_reason=max_tokens)", stop="max_tokens")):
        res, sleeps, state = _score_with_results(lambda n, model, r=r: r)
        m = res.metrics
        assert sleeps == [] and state["n"] == 3               # no retry at all
        assert m["model_failures"] == 3 and m["api_failures_unrecovered"] == 0
        assert m["failure_pct"] == 100.0 and not res.passed
        assert m["retried_calls_candidate"] == 0 and m["retried_calls_current"] == 0
        assert ev.api_problem(m) is None                      # a FAIL, not "no verdict"
        assert m["failure_diagnostics"][0]["kind"] == ev.KIND_MODEL


def test_b1_default_failure_kind_never_retries_and_counts_as_model():
    rescore = _rescore_by_model({"cur-model": 0.0, "cand-model": 0.0}, none_for=("cand-model",))
    sleeps = []
    m = ev.eval_score([_row("AAA", "2026-10-01")], {}, rescore, current=CUR, candidate=CAND,
                      sleep=sleeps.append).metrics
    assert sleeps == [] and rescore.calls.count("cand-model") == 2
    assert m["model_failures"] == 2 and m["retried_calls_candidate"] == 0


def test_b1_retry_that_fails_again_counts_as_unrecovered_api():
    res, sleeps, state = _score_with_results(lambda n, model: _res(503, "InternalServerError: down"))
    m = res.metrics
    assert state["n"] == 6 and len(sleeps) == 3
    assert m["api_failures_unrecovered"] == 3 and m["final_calls"] == 3
    assert m["failure_pct"] == 100.0 and m["api_errors_top"][0][:2] == [503, "InternalServerError: down"]
    assert ev.api_problem(m)["failures"] == 3


def test_b2_credit_exhausted_is_an_api_problem_and_not_retried():
    res, sleeps, state = _score_with_results(
        lambda n, model: _res(400, "BadRequestError: Your credit balance is too low sk-KEY"))
    assert sleeps == [] and state["n"] == 3
    p = ev.api_problem(res.metrics, key="sk-KEY")
    assert p["failures"] == 3 and p["calls"] == 3 and p["pct"] == 100.0
    (status, err), n = p["top"][0]
    assert status == 400 and n == 3 and "sk-KEY" not in err and "***" in err


def test_b2_filter_retries_any_failure_and_records_kinds():
    tally = ev.CallTally()

    def sb(items, setting):
        tally.record(_res(None, "unexpected stop_reason: refusal", stop="refusal"))
        return None
    m = ev.eval_filter(_neg_titles(3), sb, current=CUR, candidate=CAND, sleep=lambda s: None,
                       failure_kind=lambda: ev.classify_failure(tally.last()),
                       failure_info=lambda: ev.call_info(tally.last())).metrics
    assert m["model_failures"] == 9 and m["api_failures_unrecovered"] == 0 and m["final_calls"] == 9
    assert m["retried_calls_candidate"] == 1 and m["retried_calls_current"] == 2
    assert m["failure_pct"] == 100.0 and ev.api_problem(m) is None


def test_b2_trigger_records_kinds_and_n3_diagnostics_without_retry():
    tally = ev.CallTally()
    calls = []
    real = [{"id": 7, "premortem_commitment": "Out under $90 " + "x" * 300,
             "premortem_trigger_price": None, "premortem_trigger_direction": None}]

    def extract(text, setting):
        calls.append(text)
        tally.record(_res(None, "unexpected stop_reason: refusal", stop="refusal"))
        return None
    m = ev.eval_trigger([], real, extract, candidate=CAND,
                        failure_kind=lambda: ev.classify_failure(tally.last()),
                        failure_info=lambda: ev.call_info(tally.last())).metrics
    assert len(calls) == 2                                   # two runs, no retry
    assert m["model_failures"] == 2 and m["api_failures_unrecovered"] == 0 and m["final_calls"] == 2
    d = m["failure_diagnostics"][0]
    assert d["trade_id"] == 7 and d["commitment_len"] == len(calls[0]) and len(d["commitment"]) == 160
    assert d["stop_reason"] == "refusal" and d["kind"] == ev.KIND_MODEL and "status" in d


def test_n6_real_disagreements_carry_commitment_text_capped():
    real = [{"id": 5, "premortem_commitment": "Out under $90. " + "y" * 300,
             "premortem_trigger_price": 90.0, "premortem_trigger_direction": "below"}]
    m = ev.eval_trigger([], real, lambda t, s: NOT, candidate=CAND).metrics
    assert len(m["real_disagreements"][0]["text"]) == 160


def test_e3_call_tally_keeps_stop_reason_and_last_per_thread():
    import threading
    tally = ev.CallTally()
    ok = llm.LLMResult(text="x", stop_reason="end_turn", error=None, model="m", usage=None)
    bad = _res(400, "credit balance too low sk-SECRET " + "e" * 200)
    for r in [ok] * 8 + [bad] * 2:
        tally.record(r)
    rows = tally.rows()
    assert len(rows) == 10 and all(len(r[2]) <= 120 for r in rows)
    assert rows[0][3] == "end_turn" and rows[-1][3] is None
    assert len(ev.api_failures(rows)) == 2
    assert tally.last() is bad
    seen = []
    t = threading.Thread(target=lambda: seen.append(tally.last()))
    t.start()
    t.join()
    assert seen == [None]                                    # last() is per thread
    assert ev.top_api_errors(ev.api_failures(rows), "sk-SECRET")[0][0][1].count("sk-SECRET") == 0


def test_b2_api_problem_threshold_boundary():
    n = 100
    lim = C.LLM_EVAL_MAX_FAILURE_PCT
    at = int(lim * n / 100)
    assert ev.api_problem({"final_calls": n, "api_failures_unrecovered": at}) is None
    assert ev.api_problem({"final_calls": n, "api_failures_unrecovered": at + 1}) is not None
    assert ev.api_problem({}) is None


def test_e3_script_exits_3_and_saves_nothing_on_api_problem(monkeypatch, capsys):
    script = _load_script()
    monkeypatch.setenv("SUPABASE_URL", "u")
    monkeypatch.setenv("SUPABASE_KEY", "k")
    monkeypatch.setattr(llm, "resolve_anthropic_key", lambda environ=None: "sk-KEY")
    monkeypatch.setattr(llm, "retrieve_model", lambda key, model: ({"efforts": []}, None, None))
    monkeypatch.setattr(script, "_current_setting", lambda role: CUR)
    saved = []
    monkeypatch.setattr(db, "save_llm_role_eval", lambda row: saved.append(row) or 1)
    bad = _res(400, "BadRequestError: credit balance sk-KEY low")
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: bad)
    real_complete = llm.complete

    def fake_eval(args, db_, ni, pm, api_key, current, candidate, tally):
        for _ in range(5):
            llm.complete("score")        # the wrapper installed by main() records these
        assert tally.last() is bad
        r = ev.eval_score([_row("AAA", "2026-10-01")], {}, lambda h, t, s: None,
                          current=CUR, candidate=CAND, sleep=lambda s: None,
                          failure_kind=lambda: ev.classify_failure(tally.last()),
                          failure_info=lambda: ev.call_info(tally.last()))
        return r
    monkeypatch.setattr(script, "_run_eval", fake_eval)
    rc = script.main(["--role", "score", "--model", "cand-model"])
    out = capsys.readouterr().out
    assert rc == 3 and saved == []
    assert "API / connection problem: no verdict" in out and "sk-KEY" not in out
    assert llm.complete is real_complete                       # restored


def test_n4_subsample_in_report_and_eval_summary(capsys):
    script = _load_script()
    res = ev.EvalResult("score", "m", None, "h" * 20, 1, "b", 5,
                        metrics={"max_rows": 40, "sample_total": 90, "history_rows": 3,
                                 "failure_diagnostics": [
                                     {"kind": "model", "trade_id": 1, "source": "real", "run": 1,
                                      "commitment_len": 3, "commitment": "abc",
                                      "stop_reason": "refusal", "status": None, "error": "e"}] * 25,
                                 "model_failures": 25})
    script._print_report(res, "score")
    out = capsys.readouterr().out
    assert "subsample 40 of 90" in out
    assert out.count("failed call [model]") == script.MAX_DIAGNOSTIC_LINES
    assert "and 5 more failed calls" in out


def test_e5_sample_rows_is_seeded_ordered_and_reports_total():
    script = _load_script()
    items = list(range(100))
    a, total = script.sample_rows(items, 10)
    b, _ = script.sample_rows(items, 10)
    assert total == 100 and a == b and len(a) == 10 and a == sorted(a)
    assert a != items[:10]
    assert a == sorted(random.Random(20261101).sample(range(100), 10))
    assert script.sample_rows(items, None) == (items, 100)
    assert script.sample_rows(items, 500) == (items, 100)


def test_e6_no_history_still_fails_and_reports_history_rows():
    rescore = _rescore_by_model({"cur-model": 0.0, "cand-model": 0.0})
    res = ev.eval_score([_row("AAA", "2026-10-01")], {}, rescore, current=CUR, candidate=CAND)
    assert res.metrics["history_rows"] == 0
    assert any("band crossings could not be measured" in f for f in res.failures)


# --- strict extractor shared with production (2026-11-01, criteria version 2) ------------------
def test_an_extractor_that_invents_a_price_is_invented_not_a_parse_failure():
    fixture = [{"id": "f1", "text": "If guidance disappoints, I'm wrong.",
                "expected": {"price": None, "direction": None}}]
    m = ev.eval_trigger(fixture, [], lambda t, s: _ok(150.0), candidate=CAND).metrics
    assert m["invented"] == 2 and m["parse_failures"] == 0


def test_a_percent_number_returned_as_a_price_is_invented():
    t = "I'm wrong if it falls 15% from here."
    assert ev.classify_trigger(t, None, None, _ok(15.0)) == ev.CLS_INVENTED
    assert ev.classify_trigger(t, None, None, _ok(15.0), expected_is_truth=False) == ev.CLS_INVENTED
    # a time unit is not a price either
    assert ev.classify_trigger("below the 200-day", None, None, _ok(200.0)) == ev.CLS_INVENTED


def test_eval_has_no_private_number_extractor_left():
    assert not hasattr(ev, "numbers_in") and not hasattr(ev, "_NUM_RE")


# --- filter sampling happens on the NEGATIVE headlines (owner run 2026-11-01) --------------------
def test_filter_max_rows_samples_the_negatives_not_all_headlines():
    import types
    script = _load_script()
    negatives = _neg_titles(200)
    positives = [{"ticker": "P", "title": f"Wonderful record profit number {i} and great success"}
                 for i in range(800)]
    heads = [{"ticker": h["ticker"], "headline": h["title"]} for h in negatives + positives]
    rows = [{"ticker": "T0", "headlines": heads}]
    seen = []

    class _DB:
        @staticmethod
        def load_sentiment_cache_sample(since, until):
            return rows

    class _NI:
        @staticmethod
        def llm_score_batch(items, api_key, setting=None):
            seen.append(len(items))
            return [None] * len(items)

    args = types.SimpleNamespace(role="filter", max_rows=150)
    tally = types.SimpleNamespace(last=lambda: None)
    res = script._run_eval(args, _DB, _NI, None, "key", CUR, CAND, tally)
    assert res.metrics["sample_total"] == 200
    assert res.metrics["max_rows"] == 150
    assert res.sample_n == 150
    assert sum(seen[:6]) >= 150      # the 150 sampled negatives went through the batches


# --- real trades whose stored price isn't in their own commitment (NB1) --------------------------
_LEGACY = {"id": 9, "premortem_commitment": "I'm wrong if it closes below the 200-day",
           "premortem_trigger_price": 200.0, "premortem_trigger_direction": "below"}


def test_legacy_stored_price_not_in_commitment_is_unlabelled_not_a_miss():
    m = ev.eval_trigger([], [_LEGACY], lambda t, s: NOT, candidate=CAND).metrics
    assert m["real_other"] == 0 and m["misses"] == 0
    assert m["real_disagreements"]
    for d in m["real_disagreements"]:
        assert d["unlabelled"] is True and d["reason"] == "stored price not in commitment"


def test_legacy_stored_price_candidate_returning_it_is_still_invented():
    m = ev.eval_trigger([], [_LEGACY], lambda t, s: _ok(200.0), candidate=CAND).metrics
    assert m["invented"] == 2 and m["real_other"] == 0


def test_real_trade_with_its_price_in_the_text_stays_labelled():
    row = dict(_LEGACY, premortem_commitment="I'm wrong if it closes below $200")
    m = ev.eval_trigger([], [row], lambda t, s: NOT, candidate=CAND).metrics
    assert m["real_other"] == 2 and m["misses"] == 2
    assert all("unlabelled" not in d for d in m["real_disagreements"])
