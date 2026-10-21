"""
Valuation price-target source chooser ("item C", chunk C1).

Pure computation -- no I/O, no streamlit, no db. Decides WHICH analyst price
target feeds the Valuation pillar's PT Upside leg and labels where it came
from, so no surface ever shows an unlabeled target.

Owner decision (2026-10-21): PROVIDER FIRST (the data provider's analyst mean
target and its analyst count), the owner's saved research only as a fallback,
never both. C1 is pure-additive and not wired into scoring; the thresholds
(`min_analysts`, `max_ratio`) arrive as PARAMETERS and become constants in C2.

Rules, in order:
  1. Price must be a real, finite number > 0, else nothing scores
     (`provider_reject="no_price"`), even if both sources are valid.
  2. Provider target: real number (bool/str rejected -> "not_finite"),
     finite, > 0. None -> "missing"; <= 0 -> "not_positive".
  3. Provider analyst count: an integral real number (31 or 31.0; bool and
     fractional values rejected) >= `min_analysts`. Unknown -> "analysts_unknown"
     (REJECT, owner decision); below min -> "too_few_analysts".
  4. Ratio: target / price > `max_ratio` -> "implausible". Exactly at the
     bound is accepted. There is NO lower bound (a 0.1x target is accepted;
     low targets already score 0 on the leg).
  5. Provider usable -> source "provider". Saved research is never consulted.
  6. Else saved fallback. `saved_targets`: None = the coverage read FAILED
     ("read_failed"); [] = read OK, no rows ("none"); else a list of
     {"firm", "price_target", "article_date"}. Entries whose target is not a
     finite real > 0 are dropped. Only the NEWEST entry per firm is kept
     (firm compared case-insensitively after strip; a dated entry beats an
     undated one; on an equal date the first one in the list wins). An entry
     with no usable firm name cannot be de-duplicated, so it is treated as its
     own distinct firm. Distinct firms must be >= `min_analysts`
     ("too_few_firms"); the target is the mean of the per-firm targets and the
     same ratio bound applies ("implausible").
  7. Neither usable -> source None, target None.

The returned dict holds only str/float/int/bool/None, so it is JSON/pickle safe.
"""
from __future__ import annotations

import math
import numbers
from datetime import date, datetime

SOURCE_PROVIDER = "provider"
SOURCE_SAVED = "saved"

_PROVIDER_NAMES = {"yahoo_finance": "Yahoo Finance", "fmp": "FMP"}
_GENERIC_PROVIDER = "data provider"


def _real(x) -> bool:
    """True for a finite real number that is not a bool."""
    if isinstance(x, bool) or not isinstance(x, numbers.Real):
        return False
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError, OverflowError):
        return False


def _date_str(v) -> str | None:
    """Normalise a date / datetime / ISO string to 'YYYY-MM-DD', else None."""
    if v is None:
        return None
    try:
        if isinstance(v, datetime):
            return v.date().isoformat()
        if isinstance(v, date):
            return v.isoformat()
        if hasattr(v, "date") and callable(v.date):   # pandas Timestamp etc.
            return v.date().isoformat()
        return date.fromisoformat(str(v).strip()[:10]).isoformat()
    except Exception:
        return None


def _provider_n(n):
    """Integral analyst count as int, or None when unknown/invalid."""
    if not _real(n):
        return None
    f = float(n)
    if f != int(f) or f < 0:
        return None
    return int(f)


def _pick_saved(saved_targets, min_analysts):
    """Return (target, n_firms, oldest, newest, reject)."""
    if saved_targets is None:
        return None, None, None, None, "read_failed"
    if not isinstance(saved_targets, (list, tuple)) or len(saved_targets) == 0:
        return None, None, None, None, "none"
    best: dict = {}
    anon = 0
    for entry in saved_targets:
        if not isinstance(entry, dict):
            continue
        pt = entry.get("price_target")
        if not _real(pt) or float(pt) <= 0:
            continue
        firm = entry.get("firm")
        key = firm.strip().lower() if isinstance(firm, str) and firm.strip() else None
        if key is None:
            anon += 1
            key = f"\x00anon{anon}"
        d = _date_str(entry.get("article_date"))
        cur = best.get(key)
        # newer dated entry replaces; dated beats undated; ties keep the first
        if cur is None or (d is not None and (cur[1] is None or d > cur[1])):
            best[key] = (float(pt), d)
    if len(best) < min_analysts:
        return None, len(best), None, None, "too_few_firms"
    vals = [v[0] for v in best.values()]
    dates = sorted(v[1] for v in best.values() if v[1] is not None)
    return (sum(vals) / len(vals), len(best),
            dates[0] if dates else None, dates[-1] if dates else None, None)


def choose_pt_target(provider_target, provider_n, provider_source_key, provider_as_of,
                     provider_cached, saved_targets, price, *,
                     min_analysts, max_ratio) -> dict:
    """Pick the PT-Upside target. See the module docstring for the rules."""
    out = {
        "target": None, "source": None,
        "provider_name": _PROVIDER_NAMES.get(provider_source_key, _GENERIC_PROVIDER),
        "n": None, "provider_n": _provider_n(provider_n),
        "as_of": None, "as_of_end": None, "cached": bool(provider_cached),
        "provider_reject": None, "saved_reject": None,
        "min_analysts": int(min_analysts), "max_ratio": float(max_ratio),
    }
    if not _real(price) or float(price) <= 0:
        out["provider_reject"] = "no_price"
        return out
    px = float(price)

    # ── provider ──
    reason = None
    if provider_target is None:
        reason = "missing"
    elif not _real(provider_target):
        reason = "not_finite"
    elif float(provider_target) <= 0:
        reason = "not_positive"
    elif out["provider_n"] is None:
        reason = "analysts_unknown"
    elif out["provider_n"] < min_analysts:
        reason = "too_few_analysts"
    elif float(provider_target) / px > max_ratio:
        reason = "implausible"
    if reason is None:
        out.update(target=float(provider_target), source=SOURCE_PROVIDER,
                   n=out["provider_n"], as_of=_date_str(provider_as_of))
        return out
    out["provider_reject"] = reason

    # ── saved fallback ──
    tgt, n, oldest, newest, sreason = _pick_saved(saved_targets, min_analysts)
    if sreason is None and tgt / px > max_ratio:
        sreason = "implausible"
    if sreason is None:
        out.update(target=tgt, source=SOURCE_SAVED, n=n, as_of=oldest, as_of_end=newest)
        return out
    out["saved_reject"] = sreason
    return out


def _money(x) -> str:
    return f"${float(x):,.2f}"


def _plural(n, word) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _provider_reason(c) -> str:
    r = c.get("provider_reject")
    m = c.get("min_analysts")
    k = c.get("max_ratio")
    if r == "missing":
        return "the data provider has no analyst target"
    if r in ("not_finite", "not_positive"):
        return "the data provider's target was invalid"
    if r == "analysts_unknown":
        return "the data provider didn't report how many analysts"
    if r == "too_few_analysts":
        return f"only {_plural(c.get('provider_n'), 'analyst')} at the data provider (minimum {m})"
    if r == "implausible":
        return f"the data provider's target is more than {k:g}× the price"
    if r == "no_price":
        return "no current price to compare against"
    return "the data provider's target couldn't be used"


def pt_label(choice: dict) -> str:
    """Long, human label naming the source (may contain `$`)."""
    src = choice.get("source")
    if src == SOURCE_PROVIDER:
        parts = [f"{choice['provider_name']} analyst mean target {_money(choice['target'])}",
                 _plural(choice["n"], "analyst")]
        if choice.get("as_of"):
            parts.append(f"cached copy from {choice['as_of']}" if choice.get("cached")
                         else f"fetched {choice['as_of']}")
        return " · ".join(parts)
    if src == SOURCE_SAVED:
        a, b = choice.get("as_of"), choice.get("as_of_end")
        parts = [f"Your saved research (used because {_provider_reason(choice)})",
                 f"avg of {_plural(choice['n'], 'firm target')} {_money(choice['target'])}"]
        if a and b and a != b:
            parts.append(f"dated {a}–{b}")
        elif a or b:
            parts.append(f"dated {a or b}")
        return " · ".join(parts)
    if choice.get("provider_reject") == "no_price":
        return "Not scored — no current price to compare against"
    sr = choice.get("saved_reject")
    if sr == "none":
        saved = "no saved research target in the last 90 days"
    elif sr == "read_failed":
        saved = "your saved research couldn't be read"
    elif sr == "too_few_firms":
        saved = f"fewer than {choice.get('min_analysts')} firms in your saved research"
    elif sr == "implausible":
        saved = f"your saved research's target is more than {choice.get('max_ratio'):g}× the price"
    else:
        saved = "no usable saved research target"
    return f"Not scored — {_provider_reason(choice)}; {saved}"


def pt_short_tag(choice: dict) -> str:
    """Short tag for raw-HTML interpolation. Never contains `$` or `**`."""
    src = choice.get("source")
    if src == SOURCE_PROVIDER:
        return f"{choice['provider_name']} mean, {_plural(choice['n'], 'analyst')}"
    if src == SOURCE_SAVED:
        d = choice.get("as_of_end") or choice.get("as_of")
        tag = f"your saved research, {_plural(choice['n'], 'firm')}"
        return f"{tag}, {d}" if d else tag
    return "not scored"
