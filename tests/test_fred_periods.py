"""Pure tests for stock_analyzer.fred_periods (CpiYoyGap C1,
docs/plans/cpi-yoy-gap-spec.md).  No network, no DB, no app import.

The CPI fixture is FRED CPIAUCNS, transcribed by the lead 2026-11-16 from
fredgraph.csv?id=CPIAUCNS (a blank = FRED has no value; October 2025 was never
collected).  UNRATE's 2025-10 is blank too."""
from __future__ import annotations

from datetime import date

import pytest

from stock_analyzer import fred_periods as fp
from stock_analyzer import macro_calendar as mc
from stock_analyzer.constants import REGIME_CPI_FALLBACK_MAX_MONTHS

pytestmark = pytest.mark.fast

D = date

# date -> CPIAUCNS value; None = blank row (".").
_CPI_ROWS = [
    ("2024-09-01", 315.301), ("2024-10-01", 315.664), ("2024-11-01", 315.493),
    ("2024-12-01", 315.605), ("2025-01-01", 317.671), ("2025-02-01", 319.082),
    ("2025-03-01", 319.799), ("2025-04-01", 320.795), ("2025-05-01", 321.465),
    ("2025-06-01", 322.561), ("2025-07-01", 323.048), ("2025-08-01", 323.976),
    ("2025-09-01", 324.800), ("2025-10-01", None),    ("2025-11-01", 324.122),
    ("2025-12-01", 324.054), ("2026-01-01", 325.252), ("2026-02-01", 326.785),
    ("2026-03-01", 330.213), ("2026-04-01", 333.020), ("2026-05-01", 335.123),
    ("2026-06-01", 333.952), ("2026-07-01", 333.918), ("2026-08-01", 334.980),
    ("2026-09-01", 336.940), ("2026-10-01", 337.511),
]


def _obs(rows) -> list:
    """Newest-first (date, float | None) list from (iso, value) rows."""
    return sorted(((date.fromisoformat(d), v) for d, v in rows),
                  key=lambda t: t[0], reverse=True)


def _cpi(drop=(), blank=(), extra=()):
    """CPI fixture as obs; `drop` = ISO dates removed (ABSENT rows), `blank` = ISO
    dates kept with value None ("." rows), `extra` = additional (iso, value) rows."""
    rows = [(d, None if d in blank else v) for d, v in _CPI_ROWS if d not in drop]
    return _obs(rows + list(extra))


def _pct(new, old):
    return (new - old) / old * 100


# ── parse_dated_obs ──────────────────────────────────────────────────────────

def test_parse_dot_becomes_none_and_row_is_kept_newest_first():
    raw = {"observations": [
        {"date": "2025-09-01", "value": "324.800"},
        {"date": "2025-11-01", "value": "324.122"},
        {"date": "2025-10-01", "value": "."},
    ]}
    assert fp.parse_dated_obs(raw) == [
        (D(2025, 11, 1), 324.122), (D(2025, 10, 1), None), (D(2025, 9, 1), 324.8)]


def test_parse_accepts_bare_rows_and_skips_bad_dates_and_junk():
    rows = [{"date": "2026-01-01", "value": "1.5"}, {"date": "garbage", "value": "2"},
            {"value": "3"}, "not a dict", {"date": "2026-02-01", "value": "nan"},
            {"date": "2026-03-01", "value": "x"}]
    assert fp.parse_dated_obs(rows) == [
        (D(2026, 3, 1), None), (D(2026, 2, 1), None), (D(2026, 1, 1), 1.5)]
    for junk in (None, 5, "abc", {}, {"observations": None}, []):
        assert fp.parse_dated_obs(junk) == []


# ── month arithmetic + labels ────────────────────────────────────────────────

def test_month_arithmetic_wraps_years_and_returns_first_of_month():
    assert fp.add_months(D(2026, 11, 17), 1) == D(2026, 12, 1)
    assert fp.add_months(D(2026, 12, 1), 1) == D(2027, 1, 1)
    assert fp.sub_months(D(2027, 1, 1), 1) == D(2026, 12, 1)
    assert fp.sub_months(D(2027, 1, 31), 12) == D(2026, 1, 1)
    assert fp.sub_months(D(2026, 1, 1), 3) == D(2025, 10, 1)      # quarter step
    assert fp.add_months(D(2026, 5, 9), -17) == D(2024, 12, 1)
    assert fp.month_start(D(2026, 2, 28)) == D(2026, 2, 1)


def test_format_period_month_and_quarter():
    assert fp.format_period(D(2026, 10, 1)) == "Oct 2026"
    assert fp.format_period(D(2026, 7, 1), 3) == "Q3 2026"
    assert fp.format_period(D(2026, 1, 1), 3) == "Q1 2026"
    assert fp.format_period(D(2026, 10, 1), 3) == "Q4 2026"
    assert fp.format_period(None) == ""


# ── CPI date-keyed change: the confirmed bug ─────────────────────────────────

def test_oct_2026_has_no_12_month_figure_base_oct_2025_missing():
    c = fp.change_by_period(_cpi(), "yoy_pct", D(2026, 10, 1))
    assert c["status"] == "base_missing" and c["value"] is None
    assert c["period"] == D(2026, 10, 1) and c["base_period"] == D(2025, 10, 1)
    assert c["ref_value"] == 337.511 and c["base_value"] is None


def test_sep_2026_true_12_month_change():
    c = fp.change_by_period(_cpi(), "yoy", D(2026, 9, 1))        # "yoy" alias
    assert c["status"] == "ok" and c["base_period"] == D(2025, 9, 1)
    assert c["value"] == pytest.approx(336.940 / 324.800 * 100 - 100)
    assert round(c["value"], 2) == 3.74


def test_nov_2025_is_vs_nov_2024_and_differs_from_the_positional_result():
    asof = [(d, v) for d, v in _CPI_ROWS if d <= "2025-11-01"]
    obs = _obs(asof)
    c = fp.change_by_period(obs, "yoy_pct", D(2025, 11, 1))
    assert c["status"] == "ok" and c["base_period"] == D(2024, 11, 1)
    assert c["base_value"] == 315.493
    assert c["value"] == pytest.approx(_pct(324.122, 315.493))
    vals = [v for _, v in obs if v is not None]                 # what the old code saw
    assert vals[12] == 315.664                                  # Oct 2024: a 13-month base
    assert c["value"] != pytest.approx(_pct(vals[0], vals[12]))


def test_month_label_midmonth_ref_is_normalised():
    assert fp.change_by_period(_cpi(), "yoy_pct", D(2026, 9, 17))["period"] == D(2026, 9, 1)


# ── regime fallback ──────────────────────────────────────────────────────────

def test_regime_falls_back_to_sep_2026_with_the_missing_month_named():
    r = fp.cpi_yoy_for_regime(_cpi(), REGIME_CPI_FALLBACK_MAX_MONTHS)
    assert REGIME_CPI_FALLBACK_MAX_MONTHS >= 1
    assert r["basis"] == "fallback" and r["month"] == D(2026, 9, 1)
    assert round(r["value"], 2) == 3.74
    assert r["reading_month"] == D(2026, 10, 1) and r["skipped_month"] == D(2026, 10, 1)
    assert r["missing_month"] == D(2025, 10, 1)


def test_regime_latest_when_the_newest_month_has_its_base():
    r = fp.cpi_yoy_for_regime(_cpi(drop=("2026-10-01",)), 1)
    assert r["basis"] == "latest" and r["month"] == D(2026, 9, 1)
    assert r["skipped_month"] is None and r["missing_month"] is None
    assert round(r["value"], 2) == 3.74


def test_regime_12_10_print_is_latest_true_12_month_change_equal_to_positional():
    obs = _cpi(extra=[("2026-11-01", 338.200)])
    r = fp.cpi_yoy_for_regime(obs, 1)
    assert r["basis"] == "latest" and r["month"] == D(2026, 11, 1)
    assert r["value"] == pytest.approx(_pct(338.200, 324.122))
    vals = [v for _, v in obs if v is not None]                 # positional, valued rows
    assert r["value"] == pytest.approx(_pct(vals[0], vals[12]))


@pytest.mark.parametrize("mx", [0, 1, 2, 3])
def test_regime_fallback_bound_k_equals_max_is_fallback_max_plus_one_is_unavailable(mx):
    # Reading month 2026-10. Start from a gap-free CPI (the real Oct 2025 gap is
    # filled with a HYPOTHETICAL 324.5), then blank the BASE (a year back) of
    # reading months 0..n-1 so the first usable month is exactly k = n.
    def build(blanked_k):
        bases = [fp.sub_months(D(2026, 10, 1), k + 12).isoformat() for k in range(blanked_k)]
        rows = [(d, 324.5 if d == "2025-10-01" else v) for d, v in _CPI_ROWS]
        return _obs([(d, None if d in bases else v) for d, v in rows])
    at = fp.cpi_yoy_for_regime(build(mx), mx)
    assert at["month"] == fp.sub_months(D(2026, 10, 1), mx)
    assert at["basis"] == ("latest" if mx == 0 else "fallback")
    over = fp.cpi_yoy_for_regime(build(mx + 1), mx)             # first usable is k = mx + 1
    assert over["basis"] == "unavailable" and over["value"] is None and over["month"] is None
    assert over["reading_month"] == D(2026, 10, 1)
    assert over["skipped_month"] == D(2026, 10, 1)


def test_regime_bound_uses_the_constant_value():
    # Sep 2025 blank too (base of Sep 2026): with the owner bound (1) -> unavailable.
    obs = _cpi(blank=("2025-09-01",))
    r = fp.cpi_yoy_for_regime(obs, REGIME_CPI_FALLBACK_MAX_MONTHS)
    assert (r["basis"] == "unavailable") == (REGIME_CPI_FALLBACK_MAX_MONTHS == 1)
    assert fp.cpi_yoy_for_regime(obs, 2)["month"] == D(2026, 8, 1)


@pytest.mark.parametrize("kind", ["dot", "absent"])
def test_gap_a_year_back_vs_gap_in_the_current_month(kind):
    # Gap a year back (Oct 2025 blank or absent) -> base_missing, fallback to Sep.
    obs = _cpi(blank=("2025-10-01",)) if kind == "dot" else _cpi(drop=("2025-10-01",))
    assert fp.change_by_period(obs, "yoy_pct", D(2026, 10, 1))["status"] == "base_missing"
    r = fp.cpi_yoy_for_regime(obs, 1)
    assert r["basis"] == "fallback" and r["month"] == D(2026, 9, 1)
    assert r["missing_month"] == D(2025, 10, 1)

    # Gap in the CURRENT month: a "." Oct 2026 row is the reading month (spec: the
    # newest row counts even when blank) -> ref_missing, fall back to Sep; an ABSENT
    # Oct 2026 row means the newest month IS Sep 2026 -> latest.
    if kind == "dot":
        cur = _cpi(blank=("2026-10-01",))
        assert fp.change_by_period(cur, "yoy_pct", D(2026, 10, 1))["status"] == "ref_missing"
        r2 = fp.cpi_yoy_for_regime(cur, 1)
        assert r2["basis"] == "fallback" and r2["month"] == D(2026, 9, 1)
        assert r2["missing_month"] == D(2026, 10, 1) and r2["skipped_month"] == D(2026, 10, 1)
    else:
        cur = _cpi(drop=("2026-10-01",))
        assert fp.change_by_period(cur, "yoy_pct", D(2026, 10, 1))["status"] == "ref_missing"
        r2 = fp.cpi_yoy_for_regime(cur, 1)
        assert r2["basis"] == "latest" and r2["month"] == D(2026, 9, 1)


def test_regime_no_rows_and_negative_bound():
    r = fp.cpi_yoy_for_regime([], 1)
    assert r == {"value": None, "month": None, "basis": "unavailable",
                 "reading_month": None, "skipped_month": None, "missing_month": None}
    assert fp.cpi_yoy_for_regime(_cpi(), -5)["basis"] == "unavailable"   # treated as 0


# ── January boundary ─────────────────────────────────────────────────────────

def test_january_yoy_uses_prior_january_and_mom_uses_prior_december():
    obs = _cpi(extra=[("2026-11-01", 338.2), ("2026-12-01", 338.9), ("2027-01-01", 340.0)])
    y = fp.change_by_period(obs, "yoy_pct", D(2027, 1, 1))
    assert y["base_period"] == D(2026, 1, 1) and y["base_value"] == 325.252
    assert y["value"] == pytest.approx(_pct(340.0, 325.252))
    m = fp.change_by_period(obs, "mom_pct", D(2027, 1, 1))
    assert m["base_period"] == D(2026, 12, 1)
    assert m["value"] == pytest.approx(_pct(340.0, 338.9))
    m2 = fp.change_by_period(_cpi(), "mom_pct", D(2026, 1, 1))
    assert m2["base_period"] == D(2025, 12, 1) and m2["value"] == pytest.approx(_pct(325.252, 324.054))


# ── current_and_previous ─────────────────────────────────────────────────────

def test_current_and_previous_12_10_print():
    cp = fp.current_and_previous(_cpi(extra=[("2026-11-01", 338.200)]), "yoy_pct", 1)
    assert cp["current"]["status"] == "ok" and cp["current"]["period"] == D(2026, 11, 1)
    assert cp["current"]["value"] == pytest.approx(_pct(338.200, 324.122))
    assert cp["previous"]["status"] == "base_missing"              # Oct 2026 vs Oct 2025
    assert cp["previous"]["period"] == D(2026, 10, 1) and cp["previous"]["value"] is None


def test_current_and_previous_today_oct_print_not_computable_previous_is_sep():
    cp = fp.current_and_previous(_cpi(), "yoy_pct", 1)
    assert cp["current"]["status"] == "base_missing" and cp["current"]["period"] == D(2026, 10, 1)
    assert cp["previous"]["status"] == "ok" and cp["previous"]["period"] == D(2026, 9, 1)
    assert round(cp["previous"]["value"], 2) == 3.74


def test_current_skips_a_trailing_dot_row_and_no_data():
    obs = _cpi(blank=("2026-10-01",))                            # newest row has no value yet
    assert fp.current_and_previous(obs, "yoy_pct", 1)["current"]["period"] == D(2026, 9, 1)
    for empty in ([], [(D(2026, 1, 1), None)], None):
        cp = fp.current_and_previous(empty, "yoy_pct", 1)
        assert cp["current"]["status"] == "no_data" and cp["current"]["period"] is None
        assert cp["previous"]["status"] == "no_data"


def test_gdp_previous_is_three_months_back():
    gdp = _obs([("2025-10-01", 100.0), ("2026-01-01", 102.0), ("2026-04-01", 103.0)])
    cp = fp.current_and_previous(gdp, "mom_pct", 3)
    assert cp["current"]["period"] == D(2026, 4, 1) and cp["current"]["base_period"] == D(2026, 1, 1)
    assert cp["previous"]["period"] == D(2026, 1, 1) and cp["previous"]["base_period"] == D(2025, 10, 1)
    assert cp["previous"]["value"] == pytest.approx(2.0)
    assert fp.format_period(cp["previous"]["period"], 3) == "Q1 2026"


# ── other transforms ─────────────────────────────────────────────────────────

def test_mom_gap_is_not_computable_never_a_two_month_change():
    obs = _obs([("2026-01-01", 100.0), ("2026-03-01", 110.0)])    # Feb absent
    for t in ("mom_pct", "mom_diff"):
        c = fp.change_by_period(obs, t, D(2026, 3, 1))
        assert c["status"] == "base_missing" and c["value"] is None
    blank = _obs([("2026-01-01", 100.0), ("2026-02-01", None), ("2026-03-01", 110.0)])
    assert fp.change_by_period(blank, "mom_pct", D(2026, 3, 1))["status"] == "base_missing"
    assert fp.change_by_period(blank, "mom_pct", D(2026, 2, 1))["status"] == "ref_missing"


def test_zero_base_percentage_is_not_computable_but_a_difference_is_fine():
    obs = _obs([("2026-01-01", 0.0), ("2026-02-01", 5.0)])
    p = fp.change_by_period(obs, "mom_pct", D(2026, 2, 1))
    assert p["status"] == "base_missing" and p["reason"] == "base_zero" and p["value"] is None
    assert fp.change_by_period(obs, "yoy_pct", D(2027, 1, 1))["status"] == "ref_missing"
    d = fp.change_by_period(obs, "mom_diff", D(2026, 2, 1))
    assert d["status"] == "ok" and d["value"] == 5.0
    yoy = _obs([("2025-02-01", 0.0), ("2026-02-01", 5.0)])
    assert fp.change_by_period(yoy, "yoy_pct", D(2026, 2, 1))["reason"] == "base_zero"


def test_level_needs_no_base_and_unknown_transform_raises():
    c = fp.change_by_period(_cpi(), "level", D(2026, 10, 1))
    assert c["status"] == "ok" and c["value"] == 337.511 and c["base_period"] is None
    assert fp.change_by_period(_cpi(), "level", D(2025, 10, 1))["status"] == "ref_missing"
    assert fp.change_by_period([], "level", D(2025, 10, 1))["status"] == "no_data"
    with pytest.raises(ValueError):
        fp.change_by_period(_cpi(), "bogus", D(2026, 10, 1))


def test_unrate_style_three_month_level_difference_with_the_gap():
    # UNRATE (spec): 2025-10 blank; 07 4.3, 08 4.3, 09 4.4, 11 4.5; 2026-07 4.1,
    # 08 4.1, 09 4.2, 10 4.1.  2026-01 is HYPOTHETICAL (4.4), only there to put the
    # blank month exactly 3 months back.  Old positional: obs[0] - obs[3].
    ur = _obs([("2025-07-01", 4.3), ("2025-08-01", 4.3), ("2025-09-01", 4.4),
               ("2025-10-01", None), ("2025-11-01", 4.5), ("2026-01-01", 4.4),
               ("2026-07-01", 4.1), ("2026-08-01", 4.1), ("2026-09-01", 4.2),
               ("2026-10-01", 4.1)])
    gap = fp.change_by_period(ur, "mom_diff", D(2026, 1, 1), step_months=3)
    assert gap["status"] == "base_missing" and gap["base_period"] == D(2025, 10, 1)
    ok = fp.change_by_period(ur, "mom_diff", D(2026, 10, 1), 3)     # Jul 2026 is present
    assert ok["status"] == "ok" and ok["base_period"] == D(2026, 7, 1)
    assert ok["value"] == pytest.approx(0.0)


# ── no-gap equivalence with macro_calendar._apply_transform ──────────────────

def _fmt(transform, value, unit):
    if transform == "level":
        return f"{value:.2f}{unit}"
    if transform == "mom_diff":
        return f"{value:+,.0f}{unit}"
    return f"{value:+.2f}{unit}"


@pytest.mark.parametrize("transform,unit", [
    ("yoy_pct", "%"), ("mom_pct", "%"), ("mom_diff", "K"), ("level", "%")])
def test_gap_free_series_matches_the_positional_transform_for_every_transform(transform, unit):
    months = [fp.sub_months(D(2026, 10, 1), i) for i in range(30)]       # newest first
    vals = [100.0 + 1.37 * (29 - i) + (i % 5) * 0.41 for i in range(30)]
    obs = list(zip(months, vals))
    for shift in range(0, 6):                                           # several "current" months
        cur = months[shift]
        sub = obs[shift:]
        old = mc._apply_transform([v for _, v in sub], transform, unit)
        c = fp.change_by_period(sub, transform, cur, 1)
        assert c["status"] == "ok"
        assert _fmt(transform, c["value"], unit) == old


def test_gap_free_series_current_and_previous_equal_positional_pair():
    months = [fp.sub_months(D(2026, 10, 1), i) for i in range(30)]
    vals = [200.0 + 2.1 * (29 - i) + (i % 3) * 0.7 for i in range(30)]
    obs = list(zip(months, vals))
    cp = fp.current_and_previous(obs, "yoy_pct", 1)
    assert _fmt("yoy_pct", cp["current"]["value"], "%") == mc._apply_transform(vals, "yoy_pct", "%")
    assert _fmt("yoy_pct", cp["previous"]["value"], "%") == mc._apply_transform(vals[1:], "yoy_pct", "%")
