"""Playbook vs trend: the Sector Rotation Playbook beside each sector's actual trend.

Display only (docs/bug-register.md, PlaybookVsTrend; owner-approved mockup
docs/mockups/2026-11-24-playbook-vs-trend-mockup.html, option C chart on top,
option A cards under it). 🌐 Macro -> 📡 Macro signals lists what the fixed regime
map `macro.REGIME_FAVORED` says usually leads or lags; the 📈 Market and sector
trend tab says what each sector fund has actually done. Shown apart, they read as a
contradiction ("Overweight: Energy" beside "Energy: Falling"). This module puts the
two side by side. It changes no pick, size, stop or gate: nothing reads it back, the
regime read, the map and `portfolio_macro_exposure`'s Tailwind / Headwind are not
touched, and no threshold lives here.

Pure: no streamlit, no network, no DB. `app.py` only draws what these functions
return. Every interpolated value in the HTML helpers goes through `util.safe_html`.

The one rule decided here: a row CLASHES when the map says "favoured" and the
trend label is Falling, or the map says "lags" and the label is Rising. Weakening
and Steady never clash, and a label that couldn't be read (None) never clashes.
"""
from __future__ import annotations

import math

from stock_analyzer import market_trend
from stock_analyzer.portfolio import SECTOR_ETF
from stock_analyzer.util import safe_html

SIDE_FAVOURED = "favoured"
SIDE_LAGS = "lags"

# The trend tab's labels; anything else (incl. None) reads as "not checked".
_KNOWN_LABELS = ("falling", "weakening", "rising", "steady")

_CHIP_TEXT = {
    "rising": "↗ Rising",
    "steady": "→ Steady",
    "weakening": "↘ Weakening",
    "falling": "⇊ Falling",
}
_NOT_CHECKED_CHIP = "trend not checked"
_LABEL_WORD = {
    "rising": "Rising", "steady": "Steady", "weakening": "Weakening",
    "falling": "Falling", None: "Not checked",
}

# Colours (display only). Dot / chip colour by trend label, from the approved mockup.
LABEL_COLOUR = {
    "falling": "#f87171",
    "weakening": "#fbbf24",
    "rising": "#4ade80",
    "steady": "#d1d5db",
    None: "#9ca3af",
}
_CHIP_BG = {
    "rising": "#0f2e1c", "steady": "#262b36", "weakening": "#3a2c0b",
    "falling": "#3b1416", None: "#1f2430",
}
CLASH_COLOUR = "#f59e0b"
FAVOURED_COLOUR = "#22c55e"
LAGS_COLOUR = "#ef4444"
ZONE_AGREE_FILL = "#0f2e1c"
ZONE_CLASH_FILL = "#3a2c0b"

# Chart geometry (display sizes, not decision values).
_DOT_BASE_PX = 16          # a fund the owner doesn't hold, or whose weight is unknown
_DOT_PER_PCT_PX = 0.7      # extra diameter per point of the owner's weight
_DOT_MAX_PX = 56
_Y_PAD_FACTOR = 1.6        # head-room above the largest gap so the zone captions stay clear
_Y_FALLBACK = 1.0          # axis half-height when every gap is exactly zero

HEADING = "Sector Rotation Playbook: what usually leads in this regime"
CHART_TITLE = "Playbook vs what actually happened"
Y_AXIS_TITLE = "4-week vs S&P 500 (pts)"
X_LAGS_TITLE = "◀ map: usually lags"
X_FAVOURED_TITLE = "map: usually favoured ▶"
ZONE_TEXT = {
    "top_left": "map says lags · beating the S&P 500",
    "top_right": "map says favoured · beating the S&P 500",
    "bottom_left": "map says lags · trailing the S&P 500",
    "bottom_right": "map says favoured · trailing the S&P 500",
}
CLASH_TAG = "⚠ trend disagrees"
NO_TREND_CAPTION = "Trend not checked, so the map can't be compared with what happened."
NO_POINTS_CAPTION = (
    "No sector has a 4-week figure against the S&P 500, so there is nothing to chart."
)
ALIGNMENT_CAPTION = "Tailwind / headwind = the regime map above, not each sector's trend."
CHART_LEGEND_CAPTION = (
    "Dot colour = the sector's trend (↗ Rising green, → Steady grey, ↘ Weakening amber, "
    "⇊ Falling red; grey = not checked). Amber ring = the trend disagrees with the map "
    "(favoured but Falling, or lags but Rising). Dot size = your weight: your share of your "
    "holdings' value in that fund's sector(s), at 🏠 Home's last build (small = not held, "
    "or your weight couldn't be read). Zones show only whether the fund beat or trailed "
    "the S&P 500 over the 4 weeks; the amber ring is the trend-label check."
)


# ── small helpers ────────────────────────────────────────────────────────────

def _num(v) -> float | None:
    """A finite float, else None (never a fabricated 0)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _norm_label(label) -> str | None:
    return label if label in _KNOWN_LABELS else None


def is_clash(side: str, label) -> bool:
    """The agree / clash rule. Favoured + Falling, or lags + Rising. Weakening,
    Steady and a label that couldn't be read (None / unknown) never clash."""
    lab = _norm_label(label)
    return (side == SIDE_FAVOURED and lab == "falling") or (side == SIDE_LAGS and lab == "rising")


def _weight_pct(weights, symbol: str | None) -> float | None:
    """The owner's share (percent of the book) in `symbol`, None when not held,
    unknown or not positive."""
    if symbol is None or not isinstance(weights, dict):
        return None
    entry = weights.get(symbol)
    if not isinstance(entry, dict):
        return None
    pct = _num(entry.get("pct"))
    return pct if pct is not None and pct > 0 else None


def _trend_rows_by_symbol(trend_result) -> dict:
    if not isinstance(trend_result, dict):
        return {}
    out = {}
    for r in trend_result.get("sectors", []):
        if isinstance(r, dict) and r.get("symbol") is not None:
            out[r["symbol"]] = r
    return out


def _fund_display(symbol: str | None, trend_row: dict | None) -> str:
    """'XLE', or 'Software, IGV' when the fund has its own display name (a fund
    that backs several app sectors)."""
    if symbol is None:
        return "no sector fund"
    if trend_row is None:
        return symbol
    name = trend_row.get("name")
    sectors = list(trend_row.get("app_sectors", []))
    if not name or name in sectors or name == " · ".join(sectors):
        return symbol
    return f"{name}, {symbol}"


# ── the rows ─────────────────────────────────────────────────────────────────

def rows(favored: dict, trend_result, weights, rate_sensitivity: dict | None = None) -> list[dict]:
    """One row per Playbook sector: the overweight list ("favoured"), then the
    underweight list ("lags"), each in map order.

    favored: one `macro.REGIME_FAVORED[regime]` entry.
    trend_result: `market_trend.compute_trend`'s result, or None (couldn't read).
    weights: `market_trend.held_weights`'s result, or None.
    rate_sensitivity: optional {sector: score}, carried into each row for the hover tip.

    Row keys: sector, side, fund (symbol or None), fund_display, label (one of
    falling / weakening / rising / steady, or None = not checked), vs_spx_pts (float
    or None), weight_pct (float or None), weight_known (False when `weights` is None),
    fund_sectors (every app sector the fund backs), clash (bool), rate_sensitivity (float or
    None). No trend result at all means every label is None, so nothing is flagged.
    """
    by_symbol = _trend_rows_by_symbol(trend_result)
    out: list[dict] = []
    for side, key in ((SIDE_FAVOURED, "overweight"), (SIDE_LAGS, "underweight")):
        for sector in favored.get(key, []):
            fund = SECTOR_ETF.get(sector)
            if fund == "SPY":  # the 'Other' stand-in: no sector fund to read
                fund = None
            trow = by_symbol.get(fund) if fund is not None else None
            label = _norm_label(trow.get("label")) if trow is not None else None
            rs = None
            if isinstance(rate_sensitivity, dict) and sector in rate_sensitivity:
                rs = _num(rate_sensitivity[sector])
            out.append({
                "sector": sector,
                "side": side,
                "fund": fund,
                "fund_display": _fund_display(fund, trow),
                "label": label,
                "vs_spx_pts": _num(trow.get("vs_spx_pts")) if trow is not None else None,
                "weight_pct": _weight_pct(weights, fund),
                # weights None = the book couldn't be read (unknown); a readable map
                # without this fund = not held
                "weight_known": isinstance(weights, dict),
                "fund_sectors": [k for k, v in SECTOR_ETF.items() if fund is not None and v == fund],
                "clash": is_clash(side, label),
                "rate_sensitivity": rs,
            })
    return out


def chart_points(rows_: list[dict]) -> tuple[list[dict], list[str]]:
    """Chart dots: one per (fund, side), so the sectors sharing IGV become one dot.

    Returns (points, left_out). A row with no `vs_spx_pts` can't be placed, so it is
    left off the chart and its sector name goes into `left_out` (in row order) for
    the render to name. Each point: fund, fund_display, side, sectors (list), x, y,
    label, clash, weight_pct, weight_known, fund_sectors.

    x is deterministic, never random: the dots of one side are sorted by (y, fund) and
    spread evenly inside that half, favoured in (0, 1), lags in (-1, 0), so no two
    dots of a side share an x.
    """
    merged: dict[tuple, dict] = {}
    left_out: list[str] = []
    for r in rows_:
        y = r.get("vs_spx_pts")
        if y is None or r.get("fund") is None:
            left_out.append(r["sector"])
            continue
        key = (r["fund"], r["side"])
        if key not in merged:
            merged[key] = {
                "fund": r["fund"], "fund_display": r["fund_display"], "side": r["side"],
                "sectors": [], "y": y, "label": r["label"], "clash": r["clash"],
                "weight_pct": r["weight_pct"], "weight_known": r["weight_known"],
                "fund_sectors": r["fund_sectors"],
            }
        merged[key]["sectors"].append(r["sector"])
    points: list[dict] = []
    for side, sign in ((SIDE_LAGS, -1), (SIDE_FAVOURED, 1)):
        group = sorted((p for p in merged.values() if p["side"] == side),
                       key=lambda p: (p["y"], p["fund"]))
        n = len(group)
        for i, p in enumerate(group):
            p["x"] = sign * (i + 1) / (n + 1)
            points.append(p)
    return points, left_out


def y_bound(points: list[dict]) -> float:
    """Half-height of the chart's y axis: the largest gap plus head-room (display
    geometry only)."""
    biggest = max((abs(p["y"]) for p in points), default=0.0)
    return biggest * _Y_PAD_FACTOR if biggest > 0 else _Y_FALLBACK


def dot_size(weight_pct: float | None) -> float:
    """Marker diameter in px: a small default for a fund not held (or weight
    unknown), growing with the owner's share, capped."""
    w = _num(weight_pct)
    if w is None or w <= 0:
        return float(_DOT_BASE_PX)
    return float(min(_DOT_BASE_PX + _DOT_PER_PCT_PX * w, _DOT_MAX_PX))


# ── text ─────────────────────────────────────────────────────────────────────

def chip_text(label) -> str:
    return _CHIP_TEXT.get(_norm_label(label), _NOT_CHECKED_CHIP)


def label_word(label) -> str:
    return _LABEL_WORD[_norm_label(label)]


def pts_text(v) -> str:
    """'+4.2 pts' / '−8.3 pts' (Unicode minus, 1 dp); 'n/a' when unknown."""
    f = _num(v)
    if f is None:
        return "n/a"
    r = round(f, 1) + 0.0
    sign = "+" if r > 0 else ("−" if r < 0 else "")
    return f"{sign}{abs(r):.1f} pts"


def _fig_text(v) -> str:
    return pts_text(v).removesuffix(" pts")


def weight_text(weight_pct) -> str | None:
    """'35%' (the trend tab's one rounding rule), None when not held / unknown."""
    w = _num(weight_pct)
    if w is None or w <= 0:
        return None
    return market_trend.pct_text(w)


def caption(as_of_text: str | None) -> str:
    """The caption under the heading. as_of_text = 'Mon Nov 23' (the trend result's
    session via `market_trend.fmt_session`); None when the trend couldn't be read."""
    base = ("A fixed map of what tends to do well or badly in this kind of market, "
            "not a read of current prices. ")
    if as_of_text is None:
        return (base + "Each sector's actual trend (📈 Market and sector trend) "
                "couldn't be read this run.")
    return (base + f"Each sector's actual trend (📈 Market and sector trend, as of the "
            f"{as_of_text} close) is shown beside it; where the two disagree, the trend "
            "is what has actually happened.")


def chart_title(as_of_text: str) -> str:
    return f"{CHART_TITLE} (4 weeks to the {as_of_text} close)"


def left_out_caption(names: list[str]) -> str:
    return ("Not charted (no 4-week figure against the S&P 500): " + ", ".join(names) + ".")


def none_flagged_text(side: str) -> str:
    return ("🟢 Usually favoured: none flagged" if side == SIDE_FAVOURED
            else "🔴 Usually lags: none flagged")


def column_heading(side: str) -> str:
    return "🟢 Usually favoured" if side == SIDE_FAVOURED else "🔴 Usually lags"


def point_label(p: dict) -> str:
    """The text beside a chart dot: sector(s), the gap, and 'you N%' when held. The
    fund is named in brackets when the dot's sectors are not every app sector the
    fund backs (the merged IGV dot), so the weight reads as the fund's."""
    names = " / ".join(p["sectors"])
    fund_sectors = p.get("fund_sectors")
    if fund_sectors is not None and len(p["sectors"]) != len(fund_sectors):
        names += f" ({p['fund_display']})"
    parts = f"{names} {_fig_text(p['y'])}"
    w = weight_text(p.get("weight_pct"))
    return parts + (f" · you {w}" if w else "")


UNKNOWN_WEIGHT_TEXT = "unknown (open 🏠 Home first)"


def hover_text(p: dict) -> str:
    """Hover for a dot (plotly's `<br>` markup; values escaped). The weight reads
    'not held' for a fund missing from a readable weights map, and 'unknown' when
    the weights themselves couldn't be read."""
    w = weight_text(p.get("weight_pct"))
    if w:
        weight = safe_html(w)
    elif p.get("weight_known", True) is False:
        weight = safe_html(UNKNOWN_WEIGHT_TEXT)
    else:
        weight = "not held"
    return "<br>".join([
        f"<b>{safe_html(' / '.join(p['sectors']))}</b>",
        f"Fund: {safe_html(p['fund_display'])}",
        f"Trend: {safe_html(label_word(p['label']))}",
        f"4-week vs S&P 500: {safe_html(pts_text(p['y']))}",
        f"Your weight: {weight}",
    ])


def rate_tip(rate_sensitivity) -> str | None:
    rs = _num(rate_sensitivity)
    return None if rs is None else f"Rate sensitivity {rs:+.2f}"


# ── the cards (option A) ─────────────────────────────────────────────────────

def _card_html(r: dict) -> str:
    label = r["label"]
    clash = r["clash"]
    stripe = CLASH_COLOUR if clash else (FAVOURED_COLOUR if r["side"] == SIDE_FAVOURED else LAGS_COLOUR)
    border = CLASH_COLOUR if clash else "#2a3142"
    bg = "#1d1708" if clash else "#121722"
    chip_colour = LABEL_COLOUR[_norm_label(label)]
    chip_bg = _CHIP_BG[_norm_label(label)]
    chip_weight = "600" if _norm_label(label) is None else "700"
    tip = rate_tip(r.get("rate_sensitivity"))
    title_attr = f' title="{safe_html(tip)}"' if tip else ""
    tag = (f'<div style="color:{CLASH_COLOUR};font-size:.78rem;font-weight:700;margin-top:2px">'
           f'{safe_html(CLASH_TAG)}</div>') if clash else ""
    return (
        f'<div{title_attr} style="display:flex;align-items:center;justify-content:space-between;'
        f"gap:10px;border:1px solid {border};border-left:4px solid {stripe};border-radius:8px;"
        f'padding:9px 12px;margin-bottom:8px;background:{bg}">'
        f'<div><div style="font-weight:600;color:#e5e7eb">{safe_html(r["sector"])}</div>'
        f'<div style="color:#9ca3af;font-size:.8rem">{safe_html(r["fund_display"])}</div>{tag}</div>'
        f'<span style="display:inline-flex;align-items:center;padding:2px 9px;border-radius:999px;'
        f"font-size:.82rem;font-weight:{chip_weight};white-space:nowrap;color:{chip_colour};"
        f'background:{chip_bg}">{safe_html(chip_text(label))}</span>'
        "</div>"
    )


def cards_html(rows_: list[dict], side: str) -> str:
    """One column of cards ('favoured' or 'lags') as inline-styled HTML for
    `st.html`; every interpolated value is escaped. An empty side reads
    '… none flagged'."""
    mine = [r for r in rows_ if r["side"] == side]
    if not mine:
        return f'<div style="font-weight:700;margin-bottom:8px">{safe_html(none_flagged_text(side))}</div>'
    return (f'<div style="font-weight:700;margin-bottom:8px">{safe_html(column_heading(side))}</div>'
            + "".join(_card_html(r) for r in mine))
