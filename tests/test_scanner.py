"""Tests for stock_analyzer/scanner.py — the sector/momentum scanner
(`_quick_score` composite scoring) and the day-gainer discovery net
(`_day_change_pct`, `scan_movers`). Previously zero test coverage despite
`_quick_score` being real, user-facing decision logic (Score/Signal/Trend
feed the 🔍 Scanner page). `_quick_score` and `_day_change_pct` are pure and
directly testable with a synthetic `pd.DataFrame({"Close": [...], "Volume":
[...]})` + `DatetimeIndex` — no mocking needed. `scan_sectors`/`scan_movers`
cross a real I/O boundary (`yf.download`), mocked below by replacing the
module's `yf` reference entirely.

Boundary-value note: RSI is EWM-smoothed over the whole series, so hitting
an exact literal RSI (e.g. bit-precise 40.0) via constructed OHLC data isn't
solvable in closed form. The RSI boundary fixtures below were derived via a
one-time numeric binary search (against the real `stock_analyzer.indicators
.rsi`) to find an alternating gain/loss % that lands within ~0.1 of each
threshold — the resulting close-price recipes are hardcoded as tuned
constants with the search target noted in each test's docstring/comment.
The 1M/3M momentum boundary fixtures use a simpler closed-form: a long flat
baseline with a single-day "blip" at the exact lookback index the formula
reads (`close.iloc[-21]` / `close.iloc[-63]`), which lands on an exact
percentage by construction.
"""
import numpy as np
import pandas as pd
import pytest

from stock_analyzer import scanner


# ─── builders ───────────────────────────────────────────────────────────────

def _mkdf(closes, volumes=None):
    idx = pd.date_range("2020-01-01", periods=len(closes), freq="D")
    if volumes is None:
        volumes = [1_000_000.0] * len(closes)
    return pd.DataFrame({"Close": closes, "Volume": volumes}, index=idx)


def _flat_with_blip(n, blip_index_from_end, blip_value, base=100.0):
    """A flat `base`-price series of length n with a single day's Close
    overridden at `blip_index_from_end` days before the end. Because
    sma20 only looks at the last 20 rows and mom_1m/mom_3m only look at
    exactly `close.iloc[-21]`/`close.iloc[-63]`, a blip placed exactly at
    -21 or -63 moves ONLY the momentum reading (and, via the wider sma50
    window, sma50 by a small fraction), letting us hit a momentum
    percentage exactly while sma20 (hence "price>sma20") stays untouched."""
    closes = [base] * n
    closes[-blip_index_from_end] = blip_value
    return closes


def _alt_close(g_pct, l_pct=1.0, cycles=60, start=100.0):
    """Alternating +g_pct/-l_pct close series (2*cycles+1 rows). Used to
    reach a specific real (EWM-smoothed) RSI value found via numeric search
    against stock_analyzer.indicators.rsi -- see module docstring."""
    vals = [start]
    for _ in range(cycles):
        vals.append(vals[-1] * (1 + g_pct / 100.0))
        vals.append(vals[-1] * (1 - l_pct / 100.0))
    return vals


# ─── _quick_score — <30 rows guard ───────────────────────────────────────────

def test_quick_score_29_rows_returns_none():
    closes = [100.0 + i * 0.1 for i in range(29)]
    assert scanner._quick_score("TST", _mkdf(closes)) is None


def test_quick_score_30_rows_does_not_guard_out():
    closes = [100.0 + i * 0.1 for i in range(30)]
    assert scanner._quick_score("TST", _mkdf(closes)) is not None


# ─── _quick_score — RSI bucket boundaries (source: 40 / 65 / 75) ────────────
# Point values per source: sweet spot (40<=rsi<=65) +30, rsi<40 +22,
# rsi<75 +12, else +2. Fixtures below hold trend flat at the same baseline
# in each pair (verified via the printed Trend field) so the Score delta
# between paired cases isolates exactly the RSI-bucket point difference.

def test_quick_score_rsi_at_lower_bound_40_is_sweet_spot():
    # g_pct tuned so RSI == 40.0 (searched against the real rsi()).
    df = _mkdf(_alt_close(g_pct=0.7228991312215327))
    result = scanner._quick_score("TST", df)
    assert result["RSI"] == 40.0
    assert result["Score"] == 32  # 30 (sweet spot) + 0 (trend) + 2 (mom1) + 0 (mom3)


def test_quick_score_rsi_just_below_40_is_not_sweet_spot():
    df = _mkdf(_alt_close(g_pct=0.7198700357904708))  # RSI == 39.9
    result = scanner._quick_score("TST", df)
    assert result["RSI"] == 39.9
    assert result["Score"] == 24  # 22 (rsi<40) + 0 + 2 + 0 -- 8 pts below the sweet-spot case


def test_quick_score_rsi_near_upper_bound_65_is_still_sweet_spot():
    df = _mkdf(_alt_close(g_pct=2.031342438094142))  # RSI == 64.9
    result = scanner._quick_score("TST", df)
    assert result["RSI"] == 64.9
    assert result["Score"] == 100  # 30 + 35 (strong uptrend) + 20 (mom1) + 15 (mom3) -- exact max


def test_quick_score_rsi_just_above_65_drops_out_of_sweet_spot():
    df = _mkdf(_alt_close(g_pct=2.0496501223406076))  # RSI == 65.1
    result = scanner._quick_score("TST", df)
    assert result["RSI"] == 65.1
    assert result["Score"] == 82  # 12 (rsi<75) + 35 + 20 + 15 -- 18 pts below the sweet-spot case


def test_quick_score_rsi_just_below_75_is_still_middle_bucket():
    df = _mkdf(_alt_close(g_pct=3.3200149425782426))  # RSI == 74.9
    result = scanner._quick_score("TST", df)
    assert result["RSI"] == 74.9
    assert result["Score"] == 82  # 12 (rsi<75) + 35 + 20 + 15


def test_quick_score_rsi_at_75_is_not_middle_bucket():
    df = _mkdf(_alt_close(g_pct=3.3383400270840307))  # RSI == 75.0
    result = scanner._quick_score("TST", df)
    assert result["RSI"] == 75.0
    assert result["Score"] == 72  # 2 (else) + 35 + 20 + 15 -- 10 pts below the just-below-75 case


# ─── _quick_score — trend buckets ────────────────────────────────────────────
# Point values per source: price>sma20>sma50 +35, price>sma20 (only) +20,
# price>sma50 (only) +10, else +0.

def test_quick_score_trend_strong_uptrend_bucket():
    closes = [100 * (1.01 ** i) for i in range(80)]
    result = scanner._quick_score("TST", _mkdf(closes))
    assert result["Trend"] == "⬆⬆ Strong Uptrend"
    assert result["Score"] == 72  # 2 (RSI==100, else bucket) + 35 + 20 (mom1) + 15 (mom3)


def test_quick_score_trend_downtrend_bucket():
    closes = [150 * (0.99 ** i) for i in range(80)]
    result = scanner._quick_score("TST", _mkdf(closes))
    assert result["Trend"] == "⬇ Downtrend"
    assert result["Score"] == 22  # 22 (RSI==0.0, rsi<40) + 0 (trend) + 0 (mom1) + 0 (mom3)


def test_quick_score_trend_uptrend_only_bucket():
    # A long decline followed by a sharp recent rally: price and sma20 both
    # recover above sma50 (still weighed down by the older decline), but
    # sma20 hasn't yet climbed back above sma50 -- price>sma20 only.
    closes = [150 * (0.99 ** i) for i in range(65)]
    last = closes[-1]
    for i in range(1, 16):
        closes.append(last * (1.02 ** i))
    result = scanner._quick_score("TST", _mkdf(closes))
    assert result["Trend"] == "⬆ Uptrend"
    assert result["Score"] == 42  # 2 (RSI) + 20 (trend) + 20 (mom1) + 0 (mom3)


def test_quick_score_trend_mixed_bucket():
    # A long uptrend followed by a mild recent pullback: price still above
    # sma50 (the long-run average) but has slipped under its own recent
    # sma20 -- price>sma50 only.
    closes = [80 * (1.015 ** i) for i in range(65)]
    last = closes[-1]
    for i in range(1, 16):
        closes.append(last * (0.995 ** i))
    result = scanner._quick_score("TST", _mkdf(closes))
    assert result["Trend"] == "↔ Mixed"
    assert result["Score"] == 57  # 30 (RSI) + 10 (trend) + 2 (mom1) + 15 (mom3)


# ─── _quick_score — 1-month momentum buckets (source: >8 / >3 / >0 / >-5) ──

def test_quick_score_mom1_just_below_8_is_not_top_bucket():
    df = _mkdf(_flat_with_blip(70, 21, 93.0232558139535))  # mom_1m == 7.5
    result = scanner._quick_score("TST", df)
    assert result["1M Momentum"] == 7.5
    assert result["Score"] == 54  # 30 (RSI) + 10 (trend=Mixed) + 14 (mom1) + 0 (mom3)


def test_quick_score_mom1_just_above_8_is_top_bucket():
    df = _mkdf(_flat_with_blip(70, 21, 92.16589861751153))  # mom_1m == 8.5
    result = scanner._quick_score("TST", df)
    assert result["1M Momentum"] == 8.5
    assert result["Score"] == 60  # same RSI/trend as the below-8 case, mom1 jumps 14->20 (+6)


def test_quick_score_mom1_just_below_3_is_not_second_bucket():
    df = _mkdf(_flat_with_blip(70, 21, 97.5609756097561))  # mom_1m == 2.5
    result = scanner._quick_score("TST", df)
    assert result["1M Momentum"] == 2.5
    assert result["Score"] == 47  # 30 + 10 + 7 (mom1) + 0


def test_quick_score_mom1_just_above_3_is_second_bucket():
    df = _mkdf(_flat_with_blip(70, 21, 96.61835748792271))  # mom_1m == 3.5
    result = scanner._quick_score("TST", df)
    assert result["1M Momentum"] == 3.5
    assert result["Score"] == 54  # mom1 jumps 7->14 (+7) vs the below-3 case


def test_quick_score_mom1_just_below_0_is_not_third_bucket():
    df = _mkdf(_flat_with_blip(70, 21, 100.50251256281408))  # mom_1m == -0.5
    result = scanner._quick_score("TST", df)
    assert result["1M Momentum"] == -0.5
    assert result["Score"] == 32  # 30 (RSI still sweet spot) + 0 (trend flips to Downtrend) + 2 + 0


def test_quick_score_mom1_just_above_0_is_third_bucket():
    df = _mkdf(_flat_with_blip(70, 21, 99.50248756218906))  # mom_1m == 0.5
    result = scanner._quick_score("TST", df)
    assert result["1M Momentum"] == 0.5
    assert result["Score"] == 47  # 30 + 10 (trend=Mixed here) + 7 (mom1) + 0


def test_quick_score_mom1_just_below_neg5_is_not_fourth_bucket():
    df = _mkdf(_flat_with_blip(70, 21, 105.82010582010582))  # mom_1m == -5.5
    result = scanner._quick_score("TST", df)
    assert result["1M Momentum"] == -5.5
    assert result["Score"] == 30  # 30 (RSI) + 0 (trend=Downtrend) + 0 (mom1, no bucket applies) + 0


def test_quick_score_mom1_just_above_neg5_is_fourth_bucket():
    df = _mkdf(_flat_with_blip(70, 21, 104.71204188481676))  # mom_1m == -4.5
    result = scanner._quick_score("TST", df)
    assert result["1M Momentum"] == -4.5
    assert result["Score"] == 32  # same RSI/trend as the below-(-5) case, mom1 0->2 (+2)


# ─── _quick_score — 3-month momentum buckets (source: >15 / >5 / >0) ────────

def test_quick_score_mom3_just_below_15_is_not_top_bucket():
    df = _mkdf(_flat_with_blip(70, 63, 87.33624454148472))  # mom_3m == 14.5
    result = scanner._quick_score("TST", df)
    assert result["3M Momentum"] == 14.5
    assert result["Score"] == 42  # 30 (RSI) + 0 (trend) + 2 (mom1) + 10 (mom3)


def test_quick_score_mom3_just_above_15_is_top_bucket():
    df = _mkdf(_flat_with_blip(70, 63, 86.58008658008657))  # mom_3m == 15.5
    result = scanner._quick_score("TST", df)
    assert result["3M Momentum"] == 15.5
    assert result["Score"] == 47  # mom3 jumps 10->15 (+5) vs the below-15 case


def test_quick_score_mom3_just_below_5_is_not_second_bucket():
    df = _mkdf(_flat_with_blip(70, 63, 95.69377990430623))  # mom_3m == 4.5
    result = scanner._quick_score("TST", df)
    assert result["3M Momentum"] == 4.5
    assert result["Score"] == 37  # 30 + 0 + 2 + 5 (mom3)


def test_quick_score_mom3_just_above_5_is_second_bucket():
    df = _mkdf(_flat_with_blip(70, 63, 94.7867298578199))  # mom_3m == 5.5
    result = scanner._quick_score("TST", df)
    assert result["3M Momentum"] == 5.5
    assert result["Score"] == 42  # mom3 jumps 5->10 (+5) vs the below-5 case


def test_quick_score_mom3_just_below_0_is_not_third_bucket():
    df = _mkdf(_flat_with_blip(70, 63, 100.50251256281408))  # mom_3m == -0.5
    result = scanner._quick_score("TST", df)
    assert result["3M Momentum"] == -0.5
    assert result["Score"] == 32  # 30 (RSI, still sweet-spot side) + 0 + 2 + 0


def test_quick_score_mom3_just_above_0_is_third_bucket():
    df = _mkdf(_flat_with_blip(70, 63, 99.50248756218906))  # mom_3m == 0.5
    result = scanner._quick_score("TST", df)
    assert result["3M Momentum"] == 0.5
    assert result["Score"] == 37  # mom3 0->5 (+5) vs the below-0 case


# ─── _quick_score — max score is exactly 100 ─────────────────────────────────

def test_quick_score_buckets_sum_to_exactly_100():
    # RSI sweet spot (30) + strong uptrend (35) + mom1 top bucket (20) +
    # mom3 top bucket (15) = 100 exactly -- the same fixture used for the
    # RSI-65-boundary "near upper bound" case above.
    df = _mkdf(_alt_close(g_pct=2.031342438094142))  # RSI == 64.9
    result = scanner._quick_score("TST", df)
    assert result["Score"] == 100


# ─── _quick_score — SMA-all-NaN fallback ─────────────────────────────────────

def test_quick_score_sma50_all_nan_falls_back_to_price():
    # 35 rows clears the >=30-row guard but is short of the 50-day SMA
    # window, so sma50 is NaN for every row -- the fallback (sma50=price)
    # kicks in naturally, with no mocking needed.
    result = scanner._quick_score("TST", _mkdf([100.0] * 35))
    assert result is not None
    assert result["Trend"] == "⬇ Downtrend"   # price==sma20==sma50(fallback) -> no strict >
    assert result["Score"] == 32              # 30 (RSI, flat price -> 50.0) + 0 + 2 + 0


# Note: the RSI-all-NaN and sma20-all-NaN fallback branches in the source
# (`rsi = 50.0 if rsi_s.dropna().empty`, `sma20 = price if sma20_s.dropna()
# .empty`) appear to be dead code once the >=30-row guard has passed: sma20
# always has >= 11 valid values at 30 rows (rolling(20) on 30 rows), and
# indicators.rsi()'s own `.where(avg_loss > 0, ...)` clause fills every NaN
# it would otherwise produce (confirmed by inspection + experimentation
# during test-writing) -- so `rsi_s.dropna()` is never empty for any real
# Close series of length >= 2. Flagging this rather than forcing an
# artificial (mocked) trigger, per the "no mocking" instruction for this
# function.


# ─── _quick_score — volume ratio ─────────────────────────────────────────────

def test_quick_score_volume_ratio_default_when_fewer_than_20_valid_rows():
    closes = [100.0 + (i % 3) for i in range(30)]
    volumes = [np.nan] * 15 + [2_000_000.0] * 15   # only 15 valid volume rows
    result = scanner._quick_score("TST", _mkdf(closes, volumes))
    assert result["Vol Ratio"] == 1.0


def test_quick_score_volume_ratio_computed_when_20_or_more_valid_rows():
    closes = [100.0] * 30
    volumes = [1_000_000.0] * 25 + [2_000_000.0] * 5   # last 5 days double volume
    result = scanner._quick_score("TST", _mkdf(closes, volumes))
    assert result["Vol Ratio"] != 1.0


# ─── _day_change_pct ──────────────────────────────────────────────────────────

def test_day_change_pct_zero_rows_returns_none():
    assert scanner._day_change_pct(pd.Series([], dtype=float)) is None


def test_day_change_pct_one_row_returns_none():
    assert scanner._day_change_pct(pd.Series([100.0])) is None


def test_day_change_pct_non_positive_prior_close_returns_none():
    assert scanner._day_change_pct(pd.Series([-5.0, 10.0])) is None
    assert scanner._day_change_pct(pd.Series([0.0, 10.0])) is None


def test_day_change_pct_normal_calculation():
    assert scanner._day_change_pct(pd.Series([100.0, 110.0])) == pytest.approx(10.0)


def test_day_change_pct_negative_change():
    assert scanner._day_change_pct(pd.Series([100.0, 90.0])) == pytest.approx(-10.0)


# ─── scan_sectors — ticker dedup/tagging (mocked yf.download) ──────────────

def _fake_multi_download(tickers, n=35, base=100.0):
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    data = {}
    for t in tickers:
        data[("Close", t)] = [base + i * 0.1 for i in range(n)]
        data[("Volume", t)] = [1_000_000.0] * n
    return pd.DataFrame(data, index=idx)


class _FakeYF:
    def __init__(self, df):
        self._df = df
        self.calls = 0

    def download(self, tickers, **kwargs):
        self.calls += 1
        return self._df


def test_scan_sectors_extra_ticker_not_in_selected_sector_tagged_watchlist(monkeypatch):
    sector_tickers = scanner.SECTOR_UNIVERSE["Defense & Aerospace"]
    all_tickers = sector_tickers + ["NVDA"]
    fake = _FakeYF(_fake_multi_download(all_tickers))
    monkeypatch.setattr(scanner, "yf", fake)

    result = scanner.scan_sectors(["Defense & Aerospace"], extra_tickers=["NVDA"])
    assert not result.empty
    by_ticker = dict(zip(result["Ticker"], result["Sector"]))
    assert by_ticker["NVDA"] == "Watchlist"
    assert by_ticker[sector_tickers[0]] == "Defense & Aerospace"


def test_scan_sectors_extra_ticker_already_in_sector_keeps_real_sector(monkeypatch):
    # Dedup is by ticker symbol: an extra_ticker that's already part of a
    # selected sector must NOT be re-tagged "Watchlist".
    sector_tickers = scanner.SECTOR_UNIVERSE["Defense & Aerospace"]
    fake = _FakeYF(_fake_multi_download(sector_tickers))
    monkeypatch.setattr(scanner, "yf", fake)

    result = scanner.scan_sectors(["Defense & Aerospace"], extra_tickers=[sector_tickers[0]])
    by_ticker = dict(zip(result["Ticker"], result["Sector"]))
    assert by_ticker[sector_tickers[0]] == "Defense & Aerospace"


# ─── scan_sectors — App Settings (Commit 2): `universe` param, not the ──────
# module-level default, must be what the real importer path actually reads.

def test_scan_sectors_uses_explicit_universe_param_not_module_default(monkeypatch):
    """A fake sector/ticker pair that does not exist anywhere in the real
    SECTOR_UNIVERSE is the strongest possible proof that scan_sectors reads
    the passed `universe` argument, not the module-level dict — this is
    what every real caller (app.py, cron_runner.py) now relies on after
    being rewired onto stock_analyzer.reference_data.resolve_universe."""
    # Two fake tickers (not one) so the MultiIndex yf.download shape below
    # matches the multi-ticker code path scan_sectors actually takes for a
    # >1-ticker request (a single ticker hits a different raw-shape branch,
    # unrelated to what this test is proving).
    fake_universe = {"FakeSector": ["ZZZFAKE1", "ZZZFAKE2"]}
    fake = _FakeYF(_fake_multi_download(["ZZZFAKE1", "ZZZFAKE2"]))
    monkeypatch.setattr(scanner, "yf", fake)

    result = scanner.scan_sectors(["FakeSector"], universe=fake_universe)
    assert not result.empty
    assert set(result["Ticker"]) == {"ZZZFAKE1", "ZZZFAKE2"}


def test_scan_sectors_empty_universe_param_scans_nothing_no_fallback(monkeypatch):
    """An explicit {} (the real caller's contract on a resolve_universe
    failure — see reference_data's docstrings) must scan literally nothing.
    It must NEVER silently fall back to the module-level SECTOR_UNIVERSE —
    that would be the exact silent-stale-universe fallback the design doc
    rejects (the 2026-07-14 INTC failure mode repeated on this surface)."""
    fake = _FakeYF(_fake_multi_download(list(scanner.SECTOR_UNIVERSE["Defense & Aerospace"])))
    monkeypatch.setattr(scanner, "yf", fake)

    result = scanner.scan_sectors(["Defense & Aerospace"], universe={})
    assert result.empty
    assert fake.calls == 0, "yf.download must never be called with an empty resolved universe"


# ─── scan_movers — empty ticker list skips the network call ─────────────────

def test_scan_movers_default_threshold_matches_governed_constant():
    # 2026-08-04 audit finding: the default was a bare 4.0, dormant/wrong
    # relative to the governed MOVER_MIN_DAY_GAIN_PCT (5.0) since the one
    # call site always passed the constant explicitly.
    import inspect
    from stock_analyzer.constants import MOVER_MIN_DAY_GAIN_PCT
    default = inspect.signature(scanner.scan_movers).parameters["min_day_gain_pct"].default
    assert default == MOVER_MIN_DAY_GAIN_PCT


def test_scan_movers_empty_ticker_list_skips_network_call(monkeypatch):
    fake = _FakeYF(pd.DataFrame())
    monkeypatch.setattr(scanner, "yf", fake)

    result = scanner.scan_movers([])
    assert result.empty
    assert fake.calls == 0


# ─── Macro-gate coverage invariant ──────────────────────────────────────────
# The bug this guards (found 2026-08-16): daily_briefing's macro gate resolves a
# candidate's sector via portfolio.resolve_sector(ticker, <SECTOR_UNIVERSE bucket
# label>) and then tests `sector in _macro_blocked_sectors`. A resolved sector
# absent from macro_calendar._SECTOR_IMPACT can NEVER be suppressed — it fails
# OPEN, silently, with no banner. 13 of 73 scan-universe names were in that hole
# (all of Enterprise Tech and Consumer Staples & Retail, plus BA, RIVN, PYPL),
# and the held name SPCX besides. This is the test that would have caught it.

def _macro_known_sectors() -> set:
    from stock_analyzer.macro_calendar import _SECTOR_IMPACT
    known = set()
    for _cat, mapping in _SECTOR_IMPACT.items():
        known |= {k for k in mapping if k != "__ALL__"}
    return known


def test_every_scan_universe_ticker_resolves_to_a_macro_known_sector():
    # Allowlist is deliberately EMPTY: the hole was fully closed, and an empty
    # allowlist is what makes this fail the moment a new one is opened. If a
    # future change must add an entry here, that entry is a documented gate hole
    # — not a formality.
    from stock_analyzer.portfolio import resolve_sector
    known = _macro_known_sectors()
    allowed_holes: set = set()

    unblockable = {
        t: resolve_sector(t, bucket)
        for bucket, tickers in scanner.SECTOR_UNIVERSE.items()
        for t in tickers
        if resolve_sector(t, bucket) not in known and t not in allowed_holes
    }
    assert not unblockable, (
        "these tickers can never be macro-suppressed (sector unknown to "
        f"_SECTOR_IMPACT): {unblockable}"
    )


def test_every_curated_ticker_sector_value_is_macro_known():
    # The held path resolves sector from TICKER_SECTORS, so its VALUES must be
    # covered too — SPCX ("Communications") was unblockable this way.
    from stock_analyzer.portfolio import TICKER_SECTORS
    known = _macro_known_sectors()
    unknown = {t: s for t, s in TICKER_SECTORS.items() if s not in known}
    assert not unknown, f"TICKER_SECTORS values unknown to _SECTOR_IMPACT: {unknown}"


def test_new_bucket_tickers_agree_between_pick_path_and_held_path():
    # VZ is the trap: the provider's GICS string is "Communication Services",
    # the bucket label is "Communications". Only the explicit TICKER_SECTORS
    # entry makes the two paths agree — without it a held VZ and a candidate VZ
    # would be gated differently.
    from stock_analyzer.portfolio import resolve_sector
    for ticker, bucket, gics in [
        ("VZ",  "Communications", "Communication Services"),
        ("T",   "Communications", "Communication Services"),
        ("CAT", "Industrials",    "Industrials"),
        ("GE",  "Industrials",    "Industrials"),
        ("WMT", "Consumer Staples & Retail", "Consumer Defensive"),
        ("HD",  "Consumer Staples & Retail", "Consumer Cyclical"),
    ]:
        assert resolve_sector(ticker, bucket) == resolve_sector(ticker, gics), ticker


def test_every_keyed_macro_category_covers_the_same_sector_set():
    # _macro_known_sectors() unions ACROSS categories, so a label present in
    # only one category still passes the two tests above. That hides a ragged
    # map: before 2026-08-16, "Consumer" was missing Cybersecurity/AI & Cloud
    # and "Activity" was missing those plus Consumer Tech/Healthcare.
    #
    # Inert today — both categories are MEDIUM-only in _STATIC and both gate
    # sites filter to HIGH — but promoting one Retail Sales or ISM row to HIGH
    # would silently reopen exactly the fail-open just fixed. Per-category
    # coverage is the invariant that actually holds the gate closed.
    # KNOWN DEBT, pinned rather than silently tolerated — but ONLY for the
    # categories that provably cannot gate today. The distinction is the whole
    # point, and getting it wrong once already nearly shipped a live hole:
    #
    #   Inflation   CPI HIGH ×24   → gates. Complete, no gaps.
    #   Employment  NFP HIGH ×24   → gates. Was missing Cybersecurity, which
    #                                left all 7 cyber names unsuppressible ahead
    #                                of every payrolls print. FIXED 2026-08-16,
    #                                NOT allowlisted.
    #   Consumer    Retail MEDIUM  → both gate sites filter to HIGH, so inert.
    #   Activity    no _STATIC rows at all → inert.
    #
    # Allowlisting is therefore legitimate for Consumer/Activity and would NOT
    # have been for Employment. If either ever gains a HIGH row, delete its
    # entry here and set the severities with the user first.
    known_gaps = {
        "Consumer":   {"AI & Cloud", "Cybersecurity"},
        "Activity":   {"AI & Cloud", "Consumer Tech", "Cybersecurity",
                       "Healthcare"},
    }
    from stock_analyzer.macro_calendar import _SECTOR_IMPACT
    keyed = {cat: set(m) for cat, m in _SECTOR_IMPACT.items()
             if "__ALL__" not in m}
    universe = set().union(*keyed.values())
    missing = {
        cat: sorted((universe - keys) - known_gaps.get(cat, set()))
        for cat, keys in keyed.items()
        if (universe - keys) - known_gaps.get(cat, set())
    }
    assert not missing, (
        "these macro categories don't score every known sector, so those "
        f"sectors fail open if the category ever carries a HIGH event: {missing}"
    )
    # And the debt must not silently GROW under cover of the allowlist.
    stale = {cat: sorted(gaps - (universe - keyed.get(cat, universe)))
             for cat, gaps in known_gaps.items()
             if gaps - (universe - keyed.get(cat, universe))}
    assert not stale, (
        f"allowlist lists gaps that no longer exist — tighten it: {stale}")


def test_goog_absent_while_googl_present():
    # Regression guard: scan_sectors dedups by SYMBOL, not issuer, so adding the
    # second Alphabet share class would spend two of the twelve finalist slots on
    # one company and double-count it in any future sector-weight math.
    flat = {t for names in scanner.SECTOR_UNIVERSE.values() for t in names}
    assert "GOOGL" in flat
    assert "GOOG" not in flat
