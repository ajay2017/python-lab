"""Tests for stock_analyzer.valuation_target (item C, chunk C1)."""
import json
import math
from datetime import date

import pytest

from stock_analyzer.valuation_target import choose_pt_target, pt_label, pt_short_tag

MIN, RATIO = 3, 3.0


def _saved(*items):
    return [{"firm": f, "price_target": t, "article_date": d} for f, t, d in items]


SAVED_OK = _saved(("A", 100, "2026-08-01"), ("B", 110, "2026-08-02"), ("C", 120, "2026-08-03"))


def _c(pt=200.0, n=20, key="yahoo_finance", as_of="2026-10-21", cached=False,
       saved=SAVED_OK, price=100.0, mn=MIN, ratio=RATIO):
    return choose_pt_target(pt, n, key, as_of, cached, saved, price,
                            min_analysts=mn, max_ratio=ratio)


# ── provider wins ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("saved", [SAVED_OK, [], None,
                                   _saved(("X", 50, "2026-09-01"))])
def test_provider_wins_over_any_saved(saved):
    c = _c(saved=saved)
    assert c["source"] == "provider" and c["target"] == 200.0
    assert c["provider_reject"] is None and c["saved_reject"] is None


def test_provider_wins_even_when_saved_would_differ():
    assert _c(saved=SAVED_OK)["target"] != 110.0  # saved mean is 110
    assert _c(saved=SAVED_OK)["target"] == 200.0


def test_provider_none_falls_back_to_saved():
    c = _c(pt=None)
    assert c["source"] == "saved" and c["target"] == pytest.approx(110.0)
    assert c["provider_reject"] == "missing" and c["n"] == 3


def test_both_absent():
    c = _c(pt=None, saved=[])
    assert c["source"] is None and c["target"] is None
    assert (c["provider_reject"], c["saved_reject"]) == ("missing", "none")
    c = _c(pt=None, saved=None)
    assert c["saved_reject"] == "read_failed"


# ── invalid provider targets ───────────────────────────────────────────────
@pytest.mark.parametrize("bad,reason", [
    (float("nan"), "not_finite"), (float("inf"), "not_finite"),
    (0, "not_positive"), (-5.0, "not_positive"),
    ("200", "not_finite"), (True, "not_finite"),
])
def test_invalid_provider_target_rejected(bad, reason):
    c = _c(pt=bad)
    assert c["provider_reject"] == reason
    assert c["source"] == "saved"  # never chosen as provider
    assert c["target"] == pytest.approx(110.0)


# ── analyst count ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("n", [None, float("nan"), True, "20", 3.5])
def test_provider_n_unknown_rejected(n):
    c = _c(n=n)
    assert c["provider_reject"] == "analysts_unknown" and c["source"] == "saved"


def test_provider_n_below_min_rejected_and_at_min_accepted():
    c = _c(n=MIN - 1)
    assert c["provider_reject"] == "too_few_analysts" and c["provider_n"] == 2
    assert c["source"] == "saved"
    c = _c(n=MIN)
    assert c["source"] == "provider" and c["n"] == MIN
    assert _c(n=31.0)["n"] == 31


# ── ratio ──────────────────────────────────────────────────────────────────
def test_ratio_boundaries():
    assert _c(pt=300.0)["source"] == "provider"            # exactly 3.0x
    c = _c(pt=300.01)
    assert c["provider_reject"] == "implausible" and c["source"] == "saved"
    assert _c(pt=10.0)["source"] == "provider"             # 0.1x accepted, no floor


def test_saved_ratio_bound():
    c = _c(pt=None, saved=_saved(("A", 400, "2026-08-01"), ("B", 400, "2026-08-02"),
                                 ("C", 400, "2026-08-03")))
    assert c["source"] is None and c["saved_reject"] == "implausible"
    ok = _c(pt=None, saved=_saved(("A", 300, "2026-08-01"), ("B", 300, "2026-08-02"),
                                  ("C", 300, "2026-08-03")))
    assert ok["source"] == "saved"


# ── price ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("px", [None, 0, float("nan"), -1.0, True, "100"])
def test_no_price_blocks_both_sources(px):
    c = _c(price=px)
    assert c["source"] is None and c["target"] is None
    assert c["provider_reject"] == "no_price"
    c2 = _c(price=px, pt=None)
    assert c2["source"] is None


# ── saved selection ────────────────────────────────────────────────────────
def test_newest_per_firm_wins():
    saved = _saved(("CNBC Pro", 230, "2026-07-14"), ("CNBC Pro", 140, "2026-08-12"),
                   ("B", 100, "2026-08-01"), ("C", 100, "2026-08-02"))
    c = _c(pt=None, saved=saved)
    assert c["n"] == 3
    assert c["target"] == pytest.approx((140 + 100 + 100) / 3)
    assert (c["as_of"], c["as_of_end"]) == ("2026-08-01", "2026-08-12")
    # order in the list must not matter
    c = _c(pt=None, saved=list(reversed(saved)))
    assert c["target"] == pytest.approx((140 + 100 + 100) / 3)


def test_invalid_saved_targets_filtered():
    saved = SAVED_OK + _saved(("D", float("nan"), "2026-08-04"), ("E", 0, "2026-08-04"),
                              ("F", -3, "2026-08-04"), ("G", True, "2026-08-04"),
                              ("H", "150", "2026-08-04"), ("I", None, "2026-08-04"))
    c = _c(pt=None, saved=saved)
    assert c["n"] == 3 and c["target"] == pytest.approx(110.0)


def test_distinct_firm_floor():
    saved = _saved(("A", 100, "2026-08-01"), ("A", 100, "2026-08-02"), ("B", 100, "2026-08-03"))
    c = _c(pt=None, saved=saved)
    assert c["source"] is None and c["saved_reject"] == "too_few_firms"
    assert _c(pt=None, saved=saved, mn=2)["source"] == "saved"


def test_firm_matching_case_and_whitespace_insensitive():
    saved = _saved(("CNBC Pro", 100, "2026-08-01"), (" cnbc pro ", 100, "2026-08-02"),
                   ("CNBC PRO", 100, "2026-08-03"))
    c = _c(pt=None, saved=saved)
    assert c["saved_reject"] == "too_few_firms"


def test_missing_firm_counts_as_own_firm():
    saved = _saved((None, 100, "2026-08-01"), ("", 100, "2026-08-02"), ("  ", 100, "2026-08-03"))
    c = _c(pt=None, saved=saved)
    assert c["source"] == "saved" and c["n"] == 3


def test_saved_dates_accept_date_objects_and_undated_loses():
    saved = [{"firm": "A", "price_target": 100, "article_date": date(2026, 8, 1)},
             {"firm": "A", "price_target": 999, "article_date": None},
             {"firm": "B", "price_target": 100, "article_date": "2026-08-02"},
             {"firm": "C", "price_target": 100, "article_date": "2026-08-03"}]
    c = _c(pt=None, saved=saved)
    assert c["target"] == pytest.approx(100.0) and c["as_of"] == "2026-08-01"


# ── labels ─────────────────────────────────────────────────────────────────
def test_none_vs_read_failed_labels_differ():
    a = pt_label(_c(pt=None, saved=[]), fresh_days=90)
    b = pt_label(_c(pt=None, saved=None), fresh_days=90)
    assert "no saved research target in the last 90 days" in a
    assert "your saved research couldn't be read" in b and a != b


GOLDEN = dict(pt=224.81, n=31, price=158.0)


def test_label_provider_live_and_cached():
    c = _c(**GOLDEN, ratio=3.0, as_of="2026-10-21")
    assert pt_label(c) == ("Yahoo Finance analyst mean target $224.81 · 31 analysts"
                           " · fetched 2026-10-21")
    c = _c(**GOLDEN, as_of="2026-10-19", cached=True)
    assert pt_label(c) == ("Yahoo Finance analyst mean target $224.81 · 31 analysts"
                           " · cached copy from 2026-10-19")
    assert pt_label(_c(**GOLDEN, key="fmp")).startswith("FMP analyst mean target")
    assert pt_label(_c(**GOLDEN, key=None)).startswith("data provider analyst mean target")


def test_label_saved_single_date_and_range():
    one = _c(pt=None, price=158.0,
             saved=_saved(("A", 140, "2026-08-12"), ("B", 140, "2026-08-12"), ("C", 140, "2026-08-12")))
    assert pt_label(one) == ("Your saved research (used because the data provider has no "
                             "analyst target) · avg of 3 firm targets $140.00 · dated 2026-08-12")
    rng = _c(pt=None)
    assert pt_label(rng) == ("Your saved research (used because the data provider has no "
                             "analyst target) · avg of 3 firm targets $110.00 · dated 2026-08-01–2026-08-03")
    sing = _c(pt=None, mn=1, saved=_saved(("CNBC Pro", 140, "2026-08-12")))
    assert "avg of 1 firm target $140.00 · dated 2026-08-12" in pt_label(sing)


@pytest.mark.parametrize("kw,expected", [
    (dict(pt=None), "the data provider has no analyst target"),
    (dict(pt=float("nan")), "the data provider's target was invalid"),
    (dict(pt=-1), "the data provider's target was invalid"),
    (dict(n=None), "the data provider didn't report how many analysts"),
    (dict(n=2), "only 2 analysts at the data provider (minimum 3)"),
    (dict(n=1, mn=3), "only 1 analyst at the data provider (minimum 3)"),
    (dict(pt=400.0), "the data provider's target is more than 3× the price"),
])
def test_provider_reason_text_in_saved_fallback(kw, expected):
    c = _c(**kw)
    assert c["source"] == "saved"
    assert f"(used because {expected})" in pt_label(c)


@pytest.mark.parametrize("saved,part", [
    ([], "no saved research target in the last 90 days"),
    (None, "your saved research couldn't be read"),
    (_saved(("A", 100, "2026-08-01")), "fewer than 3 firms in your saved research"),
    (_saved(("A", 900, "2026-08-01"), ("B", 900, "2026-08-02"), ("C", 900, "2026-08-03")),
     "your saved research's target is more than 3× the price"),
])
def test_label_not_scored(saved, part):
    c = _c(pt=None, saved=saved)
    assert pt_label(c, fresh_days=90) == (f"Not scored — the data provider has no analyst target; {part}")


def test_label_no_price_and_fractional_ratio():
    assert pt_label(_c(price=None)) == "Not scored — no current price to compare against"
    c = _c(pt=400.0, saved=None, ratio=2.5)
    assert "more than 2.5× the price; your saved" in pt_label(c)


# ── short tag ──────────────────────────────────────────────────────────────
def test_short_tag_text():
    assert pt_short_tag(_c(**GOLDEN)) == "Yahoo Finance mean, 31 analysts"
    one = _c(pt=None, mn=1, saved=_saved(("CNBC Pro", 140, "2026-08-12")))
    assert pt_short_tag(one) == "your saved research, 1 firm, 2026-08-12"
    assert pt_short_tag(_c(pt=None)) == "your saved research, 3 firms, 2026-08-03"
    assert pt_short_tag(_c(pt=None, saved=[])) == "not scored"


def _all_variants():
    return [
        _c(**GOLDEN), _c(**GOLDEN, cached=True), _c(**GOLDEN, key="fmp"), _c(**GOLDEN, key=None),
        _c(pt=None), _c(pt=None, mn=1, saved=_saved(("CNBC Pro", 140, "2026-08-12"))),
        _c(pt=None, saved=[]), _c(pt=None, saved=None), _c(price=None), _c(pt=400.0, saved=None),
        _c(n=1), _c(pt=float("nan"), saved=None),
    ]


@pytest.mark.parametrize("choice", _all_variants())
def test_short_tag_never_has_dollar_or_bold(choice):
    tag = pt_short_tag(choice)
    assert "$" not in tag and "**" not in tag and tag


def test_long_label_does_carry_dollar_when_scored():
    assert "$" in pt_label(_c(**GOLDEN))  # guards the tag test against being trivially true


# ── golden + json ──────────────────────────────────────────────────────────
def test_spcx_golden_case():
    c = choose_pt_target(224.81, 31, "yahoo_finance", "2026-10-21", False,
                         [{"firm": "CNBC Pro", "price_target": 140, "article_date": "2026-08-12"}],
                         158.0, min_analysts=3, max_ratio=3.0)
    assert c["source"] == "provider" and c["target"] == 224.81
    assert (224.81 / 158 - 1) == pytest.approx(0.4228, abs=0.005)


@pytest.mark.parametrize("choice", _all_variants())
def test_result_is_json_safe(choice):
    json.loads(json.dumps(choice, allow_nan=False))
    assert all(v is None or isinstance(v, (str, float, int, bool)) for v in choice.values())
    assert not any(isinstance(v, float) and not math.isfinite(v) for v in choice.values())


# ── C3: base_target_note / pt_label fresh_days ───────────────────────────────
def _ch(**kw):
    base = dict(provider_target=224.81, provider_n=31, provider_source_key="yahoo_finance",
                provider_as_of="2026-10-20", provider_cached=False, saved_targets=[],
                price=117.0, min_analysts=3, max_ratio=3.0)
    base.update(kw)
    return choose_pt_target(**base)


def test_base_target_note_branches():
    from stock_analyzer.valuation_target import base_target_note
    prov = _ch()
    n = base_target_note({"base_source": "analyst"}, prov)
    assert n == "Base target: Yahoo Finance mean, 31 analysts"
    saved = _ch(provider_n=1, saved_targets=[
        {"firm": f, "price_target": 150.0, "article_date": "2026-10-01"} for f in "ABC"])
    n2 = base_target_note({"base_source": "analyst"}, saved)
    assert n2.startswith("Base target: your saved research, 3 firms, 2026-10-01")
    assert n2.endswith("(no data-provider target qualified)")
    none = _ch(provider_target=None)
    n3 = base_target_note({"base_source": "resistance"}, none)
    assert n3 == "Base target: nearest resistance — no analyst target qualified"
    n4 = base_target_note({"base_source": "momentum"}, prov)
    assert n4 == ("Base target: 3-month momentum projection — the price is above "
                  "the analyst target (Yahoo Finance mean, 31 analysts)")
    n5 = base_target_note({"base_source": "flat", "base_pct": 10.0}, none)
    assert n5 == "Base target: a flat +10% placeholder — no analyst target qualified"
    n6 = base_target_note({"base_source": "flat", "base_pct": None}, none)
    assert n6 == "Base target: a flat placeholder — no analyst target qualified"
    assert base_target_note({"base_source": "technical"}, none) is None
    for out in (n, n2, n3, n4, n5, n6):
        assert "$" not in out and "**" not in out


@pytest.mark.parametrize("targets,choice", [
    (None, None), ("x", {}), ({}, {}), ({"base_source": "weird"}, {}),
    ({"base_source": "analyst"}, None),
    ({"base_source": "analyst"}, {"source": None, "target": None}),
    ({"base_source": "resistance"}, None),
])
def test_base_target_note_none_cases(targets, choice):
    from stock_analyzer.valuation_target import base_target_note
    assert base_target_note(targets, choice) is None


def test_pt_label_none_saved_uses_fresh_days():
    c = _ch(provider_target=None, saved_targets=[])
    assert "in the last 90 days" in pt_label(c, fresh_days=90)
    assert "no recent saved research target" in pt_label(c)
