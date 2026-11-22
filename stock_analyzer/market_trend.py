"""Market and sector trend labels (display only; docs/plans/market-sector-trend.md).

Labels the S&P 500, the Nasdaq and every `portfolio.SECTOR_ETF` fund Rising /
Weakening / Falling / Steady from CLOSES UP TO an as-of session. It changes no
pick, size, stop or gate: the label is shown beside a named fund or index and
nothing reads it back as a decision input.

Pure: no streamlit, no network, no DB. Every threshold comparison lives here
(cut-offs are in `constants.py`: the `TREND_*` block plus the shared
`RISK_OFF_TREND_MA` for the index 200-day leg) so `app.py` only renders.

Rule (per session d, on closes dated <= d only, never a later bar):
  * below50 / above50: close vs the mean of the last TREND_MA_SESSIONS valid
    closes, strict; equal is neither ("at").
  * rising50: that mean is above its value TREND_MA_SLOPE_SESSIONS closes earlier.
  * trails (sector funds): the fund's 4-week return, minus the S&P 500's, both
    rounded to 1 dp BEFORE the subtraction, is below -TREND_LAG_SPX_PTS. The
    decision uses the displayed figure.
  * below200 (index rows): `exit_advisor.risk_off_regime`'s arithmetic (mean of
    the last RISK_OFF_TREND_MA valid closes, unrounded, strict <), replicated
    here so this module never imports exit_advisor; a test pins the two together.
  Falling = both legs on TREND_CONFIRM_SESSIONS NYSE sessions in a row.
  Weakening = at least one leg at the as-of session, Falling not confirmed.
  Rising = above50 and rising50 with no weakness leg. Otherwise Steady.
"""
from __future__ import annotations

import bisect
import math
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

import numpy as np
import pandas as pd

from stock_analyzer.constants import (
    RISK_OFF_TREND_MA,
    TREND_BEAR_PCT,
    TREND_CONFIRM_SESSIONS,
    TREND_CORRECTION_PCT,
    TREND_HIGH_LOOKBACK_SESSIONS,
    TREND_LAG_SPX_PTS,
    TREND_MA_SESSIONS,
    TREND_MA_SLOPE_SESSIONS,
    TREND_RETURN_SESSIONS,
    UNCLASSIFIED_SECTOR,
)
from stock_analyzer import market_time
from stock_analyzer.data import is_trading_day, previous_trading_day
from stock_analyzer.portfolio import SECTOR_ETF

# Display windows (NOT decisions): 1 week, 3 months, 6 months, in NYSE sessions.
_ONE_WEEK_SESSIONS = 5
_THREE_MONTH_SESSIONS = 63
_SIX_MONTH_SESSIONS = 126

# Calendar days of history to fetch before the as-of date. 400 calendar days is
# about 275 NYSE sessions, which covers the 252-session 52-week high with margin.
_FETCH_BUFFER_DAYS = 400

# Float-noise guard before 1-dp rounding: a return that is exactly -3.05 in
# decimal can be -3.0499999999999972 in floating point, which would round the
# wrong way. Nine decimals is far below any real price move.
_NOISE_DP = 9

_SPX = "^GSPC"
_NASDAQ = "^IXIC"
_INDEX_NAMES = {_SPX: "S&P 500", _NASDAQ: "Nasdaq"}
_FUND_NAMES = {"IGV": "Software"}
_NO_FUND = "_no_fund"

_LABEL_TEXT = {
    "falling": "Falling",
    "weakening": "Weakening",
    "rising": "Rising",
    "steady": "Steady",
    None: "Not checked",
}
_TABLE_GROUP_ORDER = {"falling": 0, "weakening": 1, "steady": 2, "rising": 3, None: 4}

# How many NYSE sessions back the session list must reach: the longest display
# window, plus the confirmation sessions, plus one for the anchor itself.
_SESSIONS_NEEDED = _SIX_MONTH_SESSIONS + TREND_CONFIRM_SESSIONS + 1

_ROW_KEYS = (
    "symbol", "name", "app_sectors", "label", "reason", "confirm_k", "ret_4w",
    "vs_spx_pts", "ma50_side", "ma50_run", "ma50_run_capped", "ma200_side",
    "from_high_pct", "drawdown_word", "ret_1w", "ret_3m", "ret_6m",
)


# ── small helpers ────────────────────────────────────────────────────────────

def _r1(x) -> float | None:
    """Round half AWAY from zero to 1 dp (Decimal, not Python's banker's round)."""
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    d = Decimal(str(round(f, _NOISE_DP))).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return float(d) + 0.0  # + 0.0 turns -0.0 into 0.0


def _fmt_date(d: date) -> str:
    """'Fri Nov 20' (day without a leading zero, on every platform)."""
    return f"{d.strftime('%a %b')} {d.day}"


def fmt_session(d: date) -> str:
    """Public alias of `_fmt_date` for the render layer's 'As of the … close'."""
    return _fmt_date(d)


def _minus(x: float) -> str:
    """1-dp figure with a Unicode minus for negatives."""
    s = f"{abs(x):.1f}"
    return f"−{s}" if x < 0 else s


def _side(close: float, avg: float) -> str:
    if close < avg:
        return "below"
    if close > avg:
        return "above"
    return "at"


class _Ser:
    """One symbol's valid closes: sorted dates, float values, date -> position."""
    __slots__ = ("dates", "vals", "pos")

    def __init__(self, series: pd.Series):
        s = pd.to_numeric(series, errors="coerce").dropna()
        self.dates = list(s.index)
        self.vals = s.to_numpy(dtype=float)
        self.pos = {d: i for i, d in enumerate(self.dates)}

    def __len__(self) -> int:
        return len(self.vals)


def _sma(ser: _Ser, i: int, n: int) -> float | None:
    """Mean of the n valid closes ending at position i (None if fewer exist)."""
    if i + 1 < n:
        return None
    return float(ser.vals[i + 1 - n:i + 1].mean())


def _sma_like_risk_off(ser: _Ser, i: int, n: int) -> float | None:
    """Same mean computed the way exit_advisor.risk_off_regime does it
    (`close.tail(n).mean()` on a pandas Series), so the 200-day leg agrees with it."""
    if i + 1 < n:
        return None
    return float(pd.Series(ser.vals[i + 1 - n:i + 1]).mean())


def _asof(ser: _Ser, anchor: date) -> float | None:
    """The last valid close dated <= anchor (None if the data starts later)."""
    k = bisect.bisect_right(ser.dates, anchor)
    return float(ser.vals[k - 1]) if k > 0 else None


def _ret(ser: _Ser, sessions: list[date], j: int, n: int) -> float | None:
    """Percent return from the close n NYSE sessions before sessions[j] to the
    close ON sessions[j]. The base is anchored by the calendar date, so a fund
    missing one interior bar uses the same base date as the S&P 500."""
    i = ser.pos.get(sessions[j])
    if i is None or j + n >= len(sessions):
        return None
    base = _asof(ser, sessions[j + n])
    if base is None or base == 0:
        return None
    return (float(ser.vals[i]) / base - 1.0) * 100.0


def _vs_spx(fund: _Ser, spx: _Ser, sessions: list[date], j: int) -> tuple[float | None, str | None]:
    """(vs_spx_pts, problem). problem is None, 'missing_spx' or 'history'."""
    f = _r1(_ret(fund, sessions, j, TREND_RETURN_SESSIONS))
    s_ret = _ret(spx, sessions, j, TREND_RETURN_SESSIONS)
    s = _r1(s_ret)
    if s is None:
        return None, ("missing_spx" if sessions[j] not in spx.pos else "history")
    if f is None:
        return None, "history"
    return _r1(f - s), None


def _sessions_back(as_of: date, count: int) -> list[date]:
    """as_of, then the NYSE sessions before it, newest first (count items)."""
    out = [as_of]
    while len(out) < count:
        out.append(previous_trading_day(out[-1]))
    return out


def _session_state(ser: _Ser, spx: _Ser, sessions: list[date], j: int, is_index: bool):
    """Legs on session sessions[j]. Returns ('ok', below50, second_leg, vs) or
    ('missing' | 'missing_spx' | 'history', session_date)."""
    d = sessions[j]
    i = ser.pos.get(d)
    if i is None:
        return ("missing", d)
    sma50 = _sma(ser, i, TREND_MA_SESSIONS)
    if sma50 is None:
        return ("history", d)
    below50 = bool(ser.vals[i] < sma50)
    if is_index:
        sma_long = _sma_like_risk_off(ser, i, RISK_OFF_TREND_MA)
        if sma_long is None:
            return ("history", d)
        vs, _problem = _vs_spx(ser, spx, sessions, j)  # display only for Nasdaq
        return ("ok", below50, bool(ser.vals[i] < sma_long), vs)
    vs, problem = _vs_spx(ser, spx, sessions, j)
    if problem is not None:
        return (problem, d)
    return ("ok", below50, bool(vs < -TREND_LAG_SPX_PTS), vs)


def _empty_row(symbol: str, name: str, sectors: list[str], reason: str) -> dict:
    row = {k: None for k in _ROW_KEYS}
    row.update({"symbol": symbol, "name": name, "app_sectors": list(sectors),
                "reason": reason, "ma50_run_capped": False})
    return row


def _ma50_run(ser: _Ser) -> tuple[str, int, bool]:
    """(side at the last close, consecutive valid closes on that side, capped).
    capped = the history ran out while the close was still on that side."""
    i0 = len(ser) - 1
    side0 = _side(float(ser.vals[i0]), _sma(ser, i0, TREND_MA_SESSIONS))
    run, j = 0, i0
    while True:
        avg = _sma(ser, j, TREND_MA_SESSIONS)
        if avg is None:
            return side0, run, True
        if _side(float(ser.vals[j]), avg) != side0:
            return side0, run, False
        run += 1
        j -= 1


def _build_row(symbol: str, name: str, sectors: list[str], ser: _Ser | None,
               spx: _Ser, sessions: list[date], as_of: date) -> dict:
    is_index = symbol in _INDEX_NAMES
    if ser is None or len(ser) == 0:
        return _empty_row(symbol, name, sectors, "not checked: no price data")
    if as_of not in ser.pos:
        return _empty_row(symbol, name, sectors,
                          f"not checked: no close for {_fmt_date(as_of)}")
    need = (RISK_OFF_TREND_MA + TREND_CONFIRM_SESSIONS - 1) if is_index else (
        TREND_MA_SESSIONS + TREND_MA_SLOPE_SESSIONS + TREND_CONFIRM_SESSIONS - 1)
    short = _empty_row(symbol, name, sectors,
                       f"not enough price history ({len(ser)} sessions)")
    if len(ser) < need:
        return short

    i0 = ser.pos[as_of]
    st0 = _session_state(ser, spx, sessions, 0, is_index)
    ret4 = _ret(ser, sessions, 0, TREND_RETURN_SESSIONS)
    sma50 = _sma(ser, i0, TREND_MA_SESSIONS)
    sma50_before = _sma(ser, i0 - TREND_MA_SLOPE_SESSIONS, TREND_MA_SESSIONS) if i0 - TREND_MA_SLOPE_SESSIONS >= 0 else None
    if st0[0] != "ok" or ret4 is None or sma50 is None or sma50_before is None:
        return short
    _, below50, second, vs0 = st0
    close0 = float(ser.vals[i0])
    side50 = _side(close0, sma50)
    rising50 = sma50 > sma50_before

    # Falling confirmation: consecutive sessions ending at as_of with both legs.
    k, stop = 0, None
    for j in range(TREND_CONFIRM_SESSIONS):
        st = st0 if j == 0 else _session_state(ser, spx, sessions, j, is_index)
        if st[0] == "ok" and st[1] and st[2]:
            k += 1
            continue
        stop = st
        break

    long_text = f"{RISK_OFF_TREND_MA}-day average"
    reason = None
    if k >= TREND_CONFIRM_SESSIONS:
        label = "falling"
    elif below50 or second:
        label = "weakening"
        if below50 and second:
            if stop is not None and stop[0] == "missing":
                reason = f"couldn't confirm: no close for {_fmt_date(stop[1])}"
            elif stop is not None and stop[0] == "missing_spx":
                reason = f"couldn't confirm: no S&P 500 close for {_fmt_date(stop[1])}"
            elif stop is not None and stop[0] == "history":
                reason = f"couldn't confirm: not enough price history on {_fmt_date(stop[1])}"
            else:
                reason = f"both signs, day {k} of {TREND_CONFIRM_SESSIONS}"
        elif below50:
            reason = f"below its {TREND_MA_SESSIONS}-day average"
        elif is_index:
            reason = f"below its {long_text}"
        else:
            reason = f"trails the S&P 500 by {abs(vs0):.1f} pts"
    elif side50 == "above" and rising50:
        label = "rising"
    else:
        label = "steady"

    side_run = _ma50_run(ser)
    n_high = TREND_HIGH_LOOKBACK_SESSIONS
    from_high = None
    if len(ser) >= n_high:
        hi = float(ser.vals[-n_high:].max())
        if hi > 0:
            from_high = _r1((close0 / hi - 1.0) * 100.0)
    word = None
    if is_index and from_high is not None:
        if from_high <= TREND_BEAR_PCT:
            word = "bear market"
        elif from_high <= TREND_CORRECTION_PCT:
            word = "correction"

    ma200_side = None
    if is_index:
        sma_long = _sma_like_risk_off(ser, i0, RISK_OFF_TREND_MA)
        ma200_side = _side(close0, sma_long)

    row = {
        "symbol": symbol, "name": name, "app_sectors": list(sectors),
        "label": label, "reason": reason, "confirm_k": k,
        "ret_4w": _r1(ret4),
        "vs_spx_pts": None if symbol == _SPX else vs0,
        "ma50_side": side_run[0], "ma50_run": side_run[1],
        "ma50_run_capped": side_run[2], "ma200_side": ma200_side,
        "from_high_pct": from_high, "drawdown_word": word,
        "ret_1w": _r1(_ret(ser, sessions, 0, _ONE_WEEK_SESSIONS)),
        "ret_3m": _r1(_ret(ser, sessions, 0, _THREE_MONTH_SESSIONS)),
        "ret_6m": _r1(_ret(ser, sessions, 0, _SIX_MONTH_SESSIONS)),
    }
    return row


# ── public: time context ─────────────────────────────────────────────────────

def as_of_session(now_et: datetime) -> date:
    """The last COMPLETED NYSE session strictly before today (ET), all day.
    Sat / Sun / Mon -> Friday; Thanksgiving Thu and Fri -> the Wednesday before.
    A tz-aware input is converted to America/New_York before its date is taken
    (so a UTC clock at 02:30 on Sat reads as Fri evening ET); a naive input is
    taken as already ET."""
    if now_et.tzinfo is not None:
        now_et = now_et.astimezone(market_time.ET)
    return previous_trading_day(now_et.date())


def history_start(as_of: date) -> date:
    """First date to fetch so the 252-session high and the 200-day average fit."""
    return as_of - timedelta(days=_FETCH_BUFFER_DAYS)


def trend_symbols() -> list[str]:
    """Symbols to download: the two indices, then every distinct sector fund
    except SPY (the 'Other' bucket's stand-in), in SECTOR_ETF order."""
    out = [_SPX, _NASDAQ]
    for fund in SECTOR_ETF.values():
        if fund != "SPY" and fund not in out:
            out.append(fund)
    return out


def _fund_sectors() -> dict[str, list[str]]:
    m: dict[str, list[str]] = {}
    for sector, fund in SECTOR_ETF.items():
        if fund == "SPY":
            continue
        m.setdefault(fund, []).append(sector)
    return m


def _fund_name(fund: str, sectors: list[str]) -> str:
    if fund in _FUND_NAMES:
        return _FUND_NAMES[fund]
    if len(sectors) == 1:
        return sectors[0]
    return " · ".join(sectors)


# ── public: the trend read ───────────────────────────────────────────────────

def _normalise(closes) -> pd.DataFrame | None:
    if closes is None or getattr(closes, "empty", True):
        return None
    df = closes.copy()
    df.index = [pd.Timestamp(x).date() for x in df.index]
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


def compute_trend(closes, *, as_of: date) -> dict | None:
    """Trend labels from closes up to `as_of`. None = couldn't read (no frame, no
    S&P 500 close ON as_of, or the S&P 500 lacks history for its own legs).

    closes: DataFrame, index = dates (DatetimeIndex or date), columns = symbols.
    Rows dated after as_of are dropped FIRST, so a later bar never changes a label.
    """
    df = _normalise(closes)
    if df is None:
        return None
    df = df[df.index <= as_of]
    if df.empty or _SPX not in df.columns:
        return None
    sessions = _sessions_back(as_of, _SESSIONS_NEEDED)
    sers = {c: _Ser(df[c]) for c in df.columns}
    spx = sers[_SPX]
    if as_of not in spx.pos:
        return None
    if len(spx) < RISK_OFF_TREND_MA + TREND_CONFIRM_SESSIONS - 1:
        return None
    if _ret(spx, sessions, 0, TREND_RETURN_SESSIONS) is None:
        return None

    indices = [
        _build_row(sym, _INDEX_NAMES[sym], [], sers.get(sym), spx, sessions, as_of)
        for sym in (_SPX, _NASDAQ)
    ]
    sectors = []
    for fund, secs in _fund_sectors().items():
        sectors.append(_build_row(fund, _fund_name(fund, secs), secs,
                                  sers.get(fund), spx, sessions, as_of))
    not_checked = [r["symbol"] for r in indices + sectors if r["label"] is None]
    return {"as_of": as_of, "indices": indices, "sectors": sectors,
            "not_checked": not_checked}


# ── public: the book's weight in each fund ───────────────────────────────────

def held_weights(port_df) -> dict | None:
    """Share of the book's Market Value per sector fund.

    None = the book isn't loaded (no frame, no rows, missing columns, or no
    positive value to weigh). Else {fund_or_'_no_fund': {pct, tickers, sectors}};
    a missing / 'Other' / SPY-mapped sector goes to '_no_fund'. IGV sums its
    three app sectors. NaN and non-positive Market Value rows are skipped.
    """
    if port_df is None or getattr(port_df, "empty", True):
        return None
    for col in ("Ticker", "Sector", "Market Value"):
        if col not in port_df.columns:
            return None
    buckets: dict[str, dict] = {}
    total = 0.0
    for tk, sec, mv in zip(port_df["Ticker"], port_df["Sector"], port_df["Market Value"]):
        try:
            v = float(mv)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(v) or v <= 0:
            continue
        sector = sec if isinstance(sec, str) else None
        fund = SECTOR_ETF.get(sector) if sector is not None else None
        if fund is None or sector == UNCLASSIFIED_SECTOR or fund == "SPY":
            fund = _NO_FUND
        b = buckets.setdefault(fund, {"value": 0.0, "tickers": [], "sectors": []})
        b["value"] += v
        total += v
        if str(tk) not in b["tickers"]:
            b["tickers"].append(str(tk))
        if sector is not None and sector not in b["sectors"]:
            b["sectors"].append(sector)
    if total <= 0:
        return None
    return {f: {"pct": b["value"] / total * 100.0, "tickers": b["tickers"],
                "sectors": b["sectors"]} for f, b in buckets.items()}


def _held_pct(weights, symbol: str) -> float | None:
    if not weights or symbol not in weights:
        return None
    return float(weights[symbol]["pct"])


def _sort_key(row: dict, weights):
    held = _held_pct(weights, row["symbol"])
    has = held is not None and held > 0
    ret = row["ret_4w"]
    return (0 if has else 1, -held if has else 0.0, ret if ret is not None else math.inf)


# ── public: shapes for the render layer ──────────────────────────────────────

def chip_groups(result: dict, weights) -> dict:
    """Sector rows that are Falling / Weakening / Rising, for the dock chips.
    Steady and not-checked funds are left out. Held funds first (largest share
    first), then the weakest 4-week return first."""
    groups: dict[str, list] = {"falling": [], "weakening": [], "rising": []}
    for r in sorted(result["sectors"], key=lambda r: _sort_key(r, weights)):
        if r["label"] in groups:
            groups[r["label"]].append({
                "name": r["name"], "symbol": r["symbol"], "ret_4w": r["ret_4w"],
                "held_pct": _held_pct(weights, r["symbol"]),
            })
    return groups


def _ma50_text(r: dict) -> str | None:
    side = r["ma50_side"]
    if side is None:
        return None
    if side == "at":
        return f"at its {TREND_MA_SESSIONS}-day"
    n = r["ma50_run"]
    if r["ma50_run_capped"]:
        return f"{side}, ≥{n} sessions"
    return f"{side}, {n} session" + ("" if n == 1 else "s")


def _pct_text(pct: float) -> str:
    if 0 < pct < 0.5:
        return "<1%"
    return f"{int(math.floor(pct + 0.5))}%"

pct_text = _pct_text  # public: the app's chips and summary line share this one rounding rule


def _weight_text(r: dict, weights) -> str:
    if r["symbol"] in _INDEX_NAMES:
        if r["symbol"] == _SPX and weights and _NO_FUND in weights:
            return f"{_pct_text(float(weights[_NO_FUND]['pct']))} no sector fund"
        return ""
    if weights is None:
        return "open 🏠 Home first"
    held = _held_pct(weights, r["symbol"])
    if held is None or held <= 0:
        return "–"
    return _pct_text(held)


def table_rows(result: dict, weights) -> list[dict]:
    """Rows for the full table: S&P 500 and Nasdaq first, then sector funds as
    Falling, Weakening, Steady, Rising, not-checked (each group held-first, then
    weakest 4-week return first)."""
    ordered = list(result["indices"]) + sorted(
        result["sectors"],
        key=lambda r: (_TABLE_GROUP_ORDER[r["label"]],) + _sort_key(r, weights))
    out = []
    for r in ordered:
        row = dict(r)
        held = _held_pct(weights, r["symbol"])
        row["label_text"] = _LABEL_TEXT[r["label"]]
        row["ma50_text"] = _ma50_text(r)
        row["held"] = bool(held is not None and held > 0)
        row["weight_text"] = _weight_text(r, weights)
        out.append(row)
    return out


def tile_line(index_row: dict) -> str | None:
    """One line under an index tile, e.g.
    'Weakening · −7.8% from high · below 50-day · above 200-day'."""
    if index_row["label"] is None:
        return None
    parts = [_LABEL_TEXT[index_row["label"]]]
    fh = index_row["from_high_pct"]
    parts.append("from high n/a" if fh is None else f"{_minus(fh)}% from high")
    if index_row["drawdown_word"]:
        parts.append(index_row["drawdown_word"])
    if index_row["ma50_side"]:
        parts.append(f"{index_row['ma50_side']} {TREND_MA_SESSIONS}-day")
    if index_row["ma200_side"]:
        parts.append(f"{index_row['ma200_side']} {RISK_OFF_TREND_MA}-day")
    return " · ".join(parts)
