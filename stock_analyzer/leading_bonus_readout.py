"""Leading-sector rank bonus readout (bug-register row TrendVsLeading).

READ-ONLY DIAGNOSTIC. Nothing here is wired into the app, changes a decision or
writes to the DB; this module imports no `db` at all. It answers one question
before anyone changes the Brief's ranking: did Grow Today / Brief picks that got
the leading-sector rank bonus beat SPY any differently from picks that did not,
and does it matter when the leading fund was itself down on the week, or the
trend calls its sector Falling (F-319, `market_trend`)?

WHAT IS AND IS NOT PERSISTED (step 0, checked 2026-11-24 against HEAD)
  * `recommendations` rows (`db.save_recommendations`) store ticker, rec_date,
    rec_type, price_at_surface, composite_score, momentum_score, the pick's
    gate-resolved `sector`, thesis (first 600 chars) and the t / bq / val pillar
    scores. They do NOT store the bonus, `_rank`, the raw scanner "Sector" label
    the bonus was matched on, an is-mover flag, or that day's leading funds.
  * So the leading set and the bonus are RECONSTRUCTED from prices (below), with
    two proxies for what the row cannot say: a mover pick is recognised by the
    thesis text `daily_briefing` puts in front of it (`MOVER_THESIS_MARKER`; a
    mover's `_rank` is its composite, never the bonus), and a row with none of
    the three pillar scores is treated as "writer unknown" (the cron scan lane
    leaves them NULL and ran with `leading_sectors = []`, so its rows never got
    the bonus; the interactive app always writes them).

THE LIVE RULE THIS MIRRORS (transcribed, not re-derived)
  * app.py `_sector_returns_ok`: one batch download of every `SECTOR_ETF` fund
    (SPY included: the "Other" bucket maps to it), each fund's own closes with
    NaN dropped, 1W = last close / the close 5 bars earlier - 1, rounded to 2 dp.
    `_fetch_sector_returns` caches it. The live "last close" is whatever bar
    Yahoo has when the Brief builds (a partial bar for today once the market is
    open); this readout uses the PRIOR completed session as primary and the
    rec_date close as a sensitivity read.
  * app.py (market_context): sort by 1W descending, take the top three; each
    entry carries `aliases` = every `SECTOR_ETF` key sharing that fund (IGV is
    AI & Cloud / AI & Data / Enterprise Tech).
  * `daily_briefing._sector_bonus` (a nested function, so it cannot be imported):
    a candidate gets the bonus when ANY leading entry's alias is a SUBSTRING of
    its sector string. `gets_leading_bonus` is that expression; a test pins the
    source text. The bonus VALUE is deliberately never used here: only whether
    it applied.

Pure: no streamlit, no network, no DB, no clock reads. Every cut-off is a
`constants.py` value; the two live-code numbers transcribed below
(`LIVE_ONE_WEEK_BARS`, `LIVE_LEADING_TOP_N`) are the Brief's own window and list
length, pinned to app.py's source by a test, not new policy.
"""
from __future__ import annotations

import math
from datetime import date
from statistics import median

import pandas as pd

from stock_analyzer import enter_now_readout as eno
from stock_analyzer.constants import (
    GATE_LEDGER_FIRM_CALLS,
    GATE_LEDGER_HORIZON_TRADING_DAYS,
    GATE_LEDGER_MIN_TICKERS,
)
from stock_analyzer.data import is_trading_day
from stock_analyzer.portfolio import SECTOR_ETF

# Transcribed from the live Brief (app.py `_sector_returns_ok` periods["1W"] and
# the `.head(3)` over the sorted frame). Pinned to the app.py source by a test.
LIVE_ONE_WEEK_BARS = 5
LIVE_LEADING_TOP_N = 3

# The thesis prefix a MOVER pick carries (daily_briefing: "Up x% today — breakout
# outside your core universe."). Pinned to the source by a test.
MOVER_THESIS_MARKER = "breakout outside your core universe"

# Provenance, not policy: the last commit that changed which funds can lead
# (2333beb6, 2026-09-02: Materials / Utilities / Real Estate got funds). Calls
# before it are reconstructed with funds that could not lead live.
FUND_SET_LAST_CHANGED = date(2026, 9, 2)

NOT_CHECKED = eno.NOT_CHECKED
EARLY_HORIZON = 10
HEADLINE_HORIZON = GATE_LEDGER_HORIZON_TRADING_DAYS
MIN_EPISODES = GATE_LEDGER_FIRM_CALLS       # the enter_now go-live floors, reused
MIN_TICKERS = GATE_LEDGER_MIN_TICKERS

TREND_ORDER = ("falling", "weakening", "steady", "rising", NOT_CHECKED)
BONUS_ORDER = ("yes", "no", "unknown")
BONUS_NAMES = {"yes": "got the leading-sector bonus", "no": "did not get it",
               "unknown": "unknown (not compared)"}
DIR_ORDER = ("up", "down", "flat")

PRE_REGISTERED_DEFINITION = (
    "PRE-REGISTERED DEFINITION (TrendVsLeading readout; written before any numbers were seen)\n"
    "  Population: Grow Today / Brief new picks (rec_type new_pick; overflow_pick only with\n"
    "    --rec-types), one episode per ticker per contiguous run of calls (the enter_now\n"
    "    readout's episode rule, graded from the episode's first call).\n"
    f"  Outcome: stock return minus SPY return at {HEADLINE_HORIZON} (primary) and "
    f"{EARLY_HORIZON} NYSE sessions after the call,\n"
    "    matured calls only. Per episode: share beating SPY (alpha > 0) + median alpha, with\n"
    "    N episodes / N tickers per cell; per call shown beside it, labelled as clustered.\n"
    "  Splits: (a) got the leading-sector bonus vs not; (b) among bonus picks, the leading\n"
    "    fund's F-319 trend label at that session; (c) among bonus picks, the leading fund's\n"
    "    own 1-week return up vs down.\n"
    f"  Floor: a cell under {MIN_EPISODES} episodes or {MIN_TICKERS} tickers prints \"not enough data\" "
    "(the enter_now go-live floors:\n"
    "    GATE_LEDGER_FIRM_CALLS / GATE_LEDGER_MIN_TICKERS; no new constant).\n"
    "  Reconstruction: the bonus and the leading funds were NOT stored, so they are rebuilt from\n"
    "    prices: SECTOR_ETF funds ranked by 1-week return (5 bars, 2 dp, as the Brief does) as of\n"
    "    the last completed session before the call, top 3, every SECTOR_ETF alias of a leading\n"
    "    fund, substring match on the pick's stored sector; F-319 label from market_trend.\n"
    "  No verdict or recommendation is printed: numbers only."
)

CAVEAT_LINE = ("CAVEAT: one market cycle, clustered calls. Calls on one name run on consecutive "
               "days inside one market, so per-call figures overstate the independent evidence; "
               "the picks shown are also the names the bonus helped rank, not the names it "
               "displaced. Numbers only, no verdict.")


# -- small helpers ------------------------------------------------------------

def _f(v):
    return eno._f(v)


def _dates_index(closes: pd.DataFrame) -> pd.DataFrame:
    """closes with a plain `date` index (already-date indexes pass through)."""
    if closes is None or getattr(closes, "empty", True):
        return pd.DataFrame()
    if len(closes.index) and isinstance(closes.index[0], date) and not isinstance(
            closes.index[0], pd.Timestamp):
        return closes
    out = closes.copy()
    out.index = [pd.Timestamp(x).date() for x in out.index]
    return out


# -- the live rule, reconstructed as of a session ------------------------------

def one_week_returns(closes: pd.DataFrame, as_of: date,
                     bars: int = LIVE_ONE_WEEK_BARS) -> tuple[dict[str, float], list[str]]:
    """({fund: 1W %}, [funds left out]) as of the close on `as_of`, in `SECTOR_ETF`
    value order, computed as app.py `_sector_returns_ok` does: the fund's own
    closes (NaN dropped), last / the close `bars` bars earlier - 1, rounded to
    2 dp. A fund with no bar ON `as_of` (or fewer than 2 closes up to it) is
    left out and named, so a caller can refuse to rank an incomplete set."""
    df = _dates_index(closes)
    out: dict[str, float] = {}
    missing: list[str] = []
    if df.empty:
        return out, list(dict.fromkeys(SECTOR_ETF.values()))
    df = df.loc[[d <= as_of for d in df.index]]
    for etf in dict.fromkeys(SECTOR_ETF.values()):
        if etf not in df.columns:
            missing.append(etf)
            continue
        col = df[etf].dropna()
        if len(col) < 2 or col.index[-1] != as_of:
            missing.append(etf)
            continue
        idx = min(bars, len(col) - 1)
        base = float(col.iloc[-idx - 1])
        if base == 0 or not math.isfinite(base):
            missing.append(etf)
            continue
        out[etf] = round((float(col.iloc[-1]) / base - 1) * 100, 2)
    return out, missing


def leading_funds(returns: dict[str, float], top_n: int = LIVE_LEADING_TOP_N) -> list[dict]:
    """The Brief's `leading_sectors` list: the top `top_n` funds by 1W return
    (descending; ties keep `SECTOR_ETF` order), each {etf, sector (first alias),
    aliases (every SECTOR_ETF key sharing the fund, IGV -> three), return_1w}."""
    ranked = sorted(returns.items(), key=lambda kv: -kv[1])   # sorted() is stable
    out = []
    for etf, r in ranked[:top_n]:
        aliases = [k for k, v in SECTOR_ETF.items() if v == etf]
        out.append({"etf": etf, "sector": aliases[0] if aliases else etf,
                    "aliases": aliases, "return_1w": float(r)})
    return out


def gets_leading_bonus(sector, leading: list[dict]) -> bool:
    """Whether `daily_briefing._sector_bonus` would add the bonus: ANY leading
    entry's alias is a substring of str(sector). Same expression as the live
    nested function (a test pins its text)."""
    return any(
        alias in str(sector)
        for ls in leading
        for alias in (ls.get("aliases") or [ls.get("sector", "")])
    )


def matched_fund(sector, leading: list[dict]) -> dict | None:
    """The best-ranked leading entry whose alias matches `sector`, else None."""
    for ls in leading:
        if any(alias in str(sector) for alias in (ls.get("aliases") or [ls.get("sector", "")])):
            return ls
    return None


def session_state(closes: pd.DataFrame, as_of: date, trend_result: dict | None) -> dict | None:
    """The reconstructed leading set for one session, or None when it can't be
    ranked honestly (no data, or ANY fund lacks a bar on `as_of`). `labels` maps
    fund -> F-319 label from `trend_result` (`market_trend.compute_trend` as of
    the same session; None -> every label reads 'not checked')."""
    rets, missing = one_week_returns(closes, as_of)
    if not rets or missing:
        return None
    labels = {r.get("symbol"): r.get("label") for r in (trend_result or {}).get("sectors", [])}
    return {"as_of": as_of, "returns": rets, "leading": leading_funds(rets),
            "labels": labels, "trend_ok": trend_result is not None}


# -- what the row can and cannot say -------------------------------------------

def is_mover_pick(thesis) -> bool | None:
    """True when the stored thesis carries the mover prefix, False for any other
    non-empty thesis, None when there is no thesis to read."""
    if not isinstance(thesis, str) or not thesis.strip():
        return None
    return MOVER_THESIS_MARKER in thesis


def has_pillar_scores(row: dict) -> bool:
    """Any of t / bq / val score persisted. Cron rows (and pre-2026-08-01 rows,
    and overflow rows) carry none: the writer is then unknown."""
    return any(_f(row.get(k)) is not None for k in ("t_score", "bq_score", "val_score"))


def bonus_state(row: dict, state: dict | None) -> tuple[str, str]:
    """('yes' | 'no' | 'unknown', reason) for one pick against a reconstructed
    session state. 'no' is certain only when the sector is outside the leading
    set (whoever wrote the row) or the pick is a mover; 'yes' needs a
    curated pick written by the interactive app; anything the row cannot settle
    is 'unknown' and is kept out of the comparisons (counted in the header)."""
    if state is None:
        return "unknown", "leading set not reconstructable that session"
    sector = row.get("sector") if isinstance(row.get("sector"), str) else ""
    if not gets_leading_bonus(sector, state["leading"]):
        return "no", "sector not in the leading set"
    mover = is_mover_pick(row.get("thesis"))
    if mover is True:
        return "no", "mover pick (ranked on composite, never the bonus)"
    if not has_pillar_scores(row):
        return "unknown", "in the leading set but no pillar scores: writer unknown (cron rows never got it)"
    if mover is None:
        return "unknown", "in the leading set but mover status unreadable (no thesis)"
    return "yes", "curated pick in the leading set, written by the app"


def week_direction(ret_1w) -> str:
    x = _f(ret_1w)
    if x is None:
        return "unknown"
    return "up" if x > 0 else ("down" if x < 0 else "flat")


def classify_pick(call: dict, state_fn) -> dict:
    """A copy of `call` plus its reconstructed leading-bonus state.

    `state_fn(session_date)` returns `session_state(...)` or None (the script
    caches it per date). Primary read: the PRIOR completed session. Sensitivity
    read: the rec_date's own close (`bonus_alt`), because the live "last close"
    may already be a partial bar for the call day. Added keys: prior_session,
    bonus, bonus_reason, bonus_alt, leading_funds, bonus_fund, fund_label,
    fund_ret_1w, fund_dir (the last four only for a 'yes' pick)."""
    c = dict(call)
    prior = eno.prior_session(call["rec_date"])
    c["prior_session"] = prior
    st = state_fn(prior)
    c["bonus"], c["bonus_reason"] = bonus_state(call, st)
    alt = state_fn(call["rec_date"]) if is_trading_day(call["rec_date"]) else None
    c["bonus_alt"] = bonus_state(call, alt)[0]
    c["leading_funds"] = ",".join(x["etf"] for x in st["leading"]) if st else ""
    c["bonus_fund"] = c["fund_label"] = c["fund_ret_1w"] = c["fund_dir"] = None
    if c["bonus"] == "yes":
        m = matched_fund(call.get("sector"), st["leading"])
        c["bonus_fund"] = m["etf"] if m else None
        lab = st["labels"].get(m["etf"]) if m else None
        c["fund_label"] = eno.label_key(lab)
        c["fund_ret_1w"] = m["return_1w"] if m else None
        c["fund_dir"] = week_direction(c["fund_ret_1w"])
    return c


# -- cells ---------------------------------------------------------------------

def group_rows(rows: list[dict], field: str, order: tuple[str, ...]) -> dict[str, list[dict]]:
    """Rows grouped by `field` (None -> 'not checked'); keys in `order` first and
    present even when empty, any other key after, sorted."""
    cells: dict[str, list[dict]] = {k: [] for k in order}
    for r in rows:
        v = r.get(field)
        cells.setdefault(NOT_CHECKED if v is None else str(v), []).append(r)
    head = [k for k in order if k in cells]
    return {k: cells[k] for k in head + sorted(k for k in cells if k not in head)}


def cell_summary(rows: list[dict], alpha_field: str,
                 min_n: int = MIN_EPISODES, min_tickers: int = MIN_TICKERS) -> dict:
    """{n, tickers, verdict, beat_share, median_alpha}. Only rows with a finite
    alpha count. Under `min_n` rows or `min_tickers` distinct tickers the verdict
    is 'not enough data' and the figures are None (withheld)."""
    vals, tickers = [], set()
    for r in rows:
        a = _f(r.get(alpha_field))
        if a is None:
            continue
        vals.append(a)
        tickers.add(str(r.get("ticker") or "").strip().upper())
    n = len(vals)
    # An empty cell (n == 0) is never measured, whatever the floors: no division.
    ok = n > 0 and n >= min_n and len(tickers) >= min_tickers
    return {"n": n, "tickers": len(tickers),
            "verdict": "measured" if ok else "not enough data",
            "beat_share": (sum(1 for x in vals if x > 0) / n) if ok else None,
            "median_alpha": float(median(vals)) if ok else None}


def split_cells(call_rows: list[dict], episode_rows: list[dict], field: str,
                order: tuple[str, ...], horizon: int, where=None) -> list[dict]:
    """Per cell: the per-EPISODE summary (the headline, floors applied) and the
    per-CALL summary (clustered; withheld whenever the episode cell is). `where`
    is an optional row predicate (e.g. bonus == 'yes') applied to both."""
    af = f"alpha_{horizon}"
    pred = where or (lambda r: True)
    ep = group_rows([r for r in episode_rows if pred(r)], field, order)
    cl = group_rows([r for r in call_rows if pred(r)], field, order)
    out = []
    for key in ep:
        e = cell_summary(ep[key], af)
        c = cell_summary(cl.get(key, []), af, min_n=0, min_tickers=0)
        if e["verdict"] != "measured":
            c = dict(c, beat_share=None, median_alpha=None)
        out.append({"cell": key, "episodes": e, "calls": c})
    return out


def _pct(x) -> str:
    return "-" if x is None else f"{x * 100:.1f}%"


def _alpha(x) -> str:
    return "-" if x is None else f"{x:+.2f}"


def format_cell_line(item: dict, names: dict | None = None) -> str:
    e, c = item["episodes"], item["calls"]
    name = (names or {}).get(item["cell"], item["cell"])
    head = f"  {name:<34} episodes {e['n']:<4} tickers {e['tickers']:<3}"
    if e["verdict"] != "measured":
        return (f"{head} not enough data (needs {MIN_EPISODES} episodes and "
                f"{MIN_TICKERS} tickers)")
    return (f"{head} beat SPY {_pct(e['beat_share']):>6}   median alpha {_alpha(e['median_alpha'])} pp"
            f"   | per call (clustered): {c['n']} calls, beat {_pct(c['beat_share'])}, "
            f"median {_alpha(c['median_alpha'])} pp")


def format_headline_line(label: str, call_rows: list[dict], episode_rows: list[dict],
                         horizon: int) -> str:
    af = f"alpha_{horizon}"
    item = {"cell": label, "episodes": cell_summary(episode_rows, af),
            "calls": cell_summary(call_rows, af, min_n=0, min_tickers=0)}
    if item["episodes"]["verdict"] != "measured":
        item["calls"] = dict(item["calls"], beat_share=None, median_alpha=None)
    return format_cell_line(item)


def bonus_counts(rows: list[dict]) -> dict:
    """Header counts: calls per bonus state and per reason, and how many calls
    read a different state under the rec_date-close sensitivity read."""
    by_state = {k: 0 for k in BONUS_ORDER}
    by_reason: dict[str, int] = {}
    differ = 0
    for r in rows:
        by_state[r["bonus"]] = by_state.get(r["bonus"], 0) + 1
        by_reason[r["bonus_reason"]] = by_reason.get(r["bonus_reason"], 0) + 1
        if r["bonus"] != r.get("bonus_alt"):
            differ += 1
    return {"by_state": by_state, "by_reason": by_reason, "differ_alt": differ, "n": len(rows)}


def split_definitions() -> list[tuple[str, str, tuple, dict | None, object]]:
    """(title, field, order, display names, where) for the three pre-registered
    splits, in print order."""
    only_yes = lambda r: r.get("bonus") == "yes"   # noqa: E731
    return [
        ("(a) Got the leading-sector bonus vs not", "bonus", BONUS_ORDER, BONUS_NAMES, None),
        ("(b) Bonus picks only: the leading fund's F-319 trend label at the prior close",
         "fund_label", TREND_ORDER, None, only_yes),
        ("(c) Bonus picks only: the leading fund's own 1-week return",
         "fund_dir", DIR_ORDER, None, only_yes),
    ]
