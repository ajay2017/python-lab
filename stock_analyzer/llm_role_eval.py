"""Eval engine for the decision LLM roles (score / filter / extract_trigger).

Chunk C2 of docs/plans/llm-model-routing.md section 17. App Settings may switch a
decision role's model only after a stored, PASSING eval of that exact model +
effort (+ capabilities hash + criteria version). This module is the pure engine:
it takes the production call functions as INJECTED callables, so it makes no
network or DB call of its own and is testable without them. The runner is
`scripts/llm_role_eval.py`; the only thing ever written is one `llm_role_evals`
row (by that script, through db.save_llm_role_eval).

Every limit lives in constants.py (`LLM_EVAL_*`) and is read at call time.
A boundary value passes (<=); one step over fails. "Net" = candidate metric minus
the noise floor (the current model compared with itself, same inputs).

Injected callables (each receives `setting`, a ``{role: llm.RoleSetting}`` mapping,
and must run that model with the production request shape):
  rescore(headlines, ticker, setting)   -> news_intelligence.rescore_headlines_llm
  score_batch(items, setting)           -> news_intelligence.llm_score_batch
  extract(text, setting)                -> premortem_monitor.extract_trigger
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Mapping

from stock_analyzer import constants as C
from stock_analyzer import llm
from stock_analyzer.news_intelligence import (
    _RESCORE_BATCH,
    _apply_suppress_only,
    _memo_key,
)
from stock_analyzer.scoring import recommendation
from stock_analyzer.sentiment import analyze_news

BASELINE_SCORE = "stored-cache + current re-run"
BASELINE_FILTER = "current re-run (live) + current re-run (noise floor)"
BASELINE_TRIGGER = "hand-labelled fixture + stored triggers"

# Alert tiers by compound, most severe first (constants.py NEWS_SENTIMENT_* block).
TIER_CLEAR, TIER_WARN, TIER_NEGATIVE, TIER_CRITICAL = 0, 1, 2, 3
_TIER_NAMES = {TIER_CLEAR: "clear", TIER_WARN: "warn",
               TIER_NEGATIVE: "negative", TIER_CRITICAL: "critical"}


# --- result type ---------------------------------------------------------------
@dataclass
class EvalResult:
    role: str
    model: str
    effort: str | None
    capabilities_hash: str
    criteria_version: int
    baseline: str
    sample_n: int
    metrics: dict = field(default_factory=dict)
    passed: bool = False
    failures: list = field(default_factory=list)

    def to_row(self, ran_by: str, code_sha: str | None = None) -> dict:
        """The `llm_role_evals` insert payload (ran_at defaults in the DB)."""
        return {
            "role": self.role, "model": self.model, "effort": self.effort,
            "capabilities_hash": self.capabilities_hash,
            "criteria_version": self.criteria_version,
            "baseline": self.baseline, "sample_n": self.sample_n,
            "metrics": self.metrics, "passed": bool(self.passed),
            "failures": list(self.failures),
            "ran_by": ran_by, "code_sha": code_sha,
        }


def capabilities_hash(caps) -> str:
    """sha256 of the canonical JSON (sorted keys, compact, lists sorted) of a
    normalized capabilities snapshot. Accepts the dict `llm.normalize_capabilities`
    / `llm.retrieve_model` returns, or a raw ModelInfo-like object (normalized
    here). None-safe: None hashes as JSON null."""
    if caps is not None and not isinstance(caps, Mapping):
        caps = llm.normalize_capabilities(caps)
    if isinstance(caps, Mapping):
        caps = {k: (sorted(v) if isinstance(v, (list, tuple, set, frozenset)) else v)
                for k, v in caps.items()}
    blob = json.dumps(caps, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --- limit tables (shared by the verdict and the script's report) ---------------
# (metric key, constant name, plain-English label)
SCORE_CHECKS = (
    ("mean_shift_net_abs", "LLM_EVAL_SCORE_MAX_MEAN_SHIFT_PTS", "net signed mean shift (pts)"),
    ("mean_abs_net", "LLM_EVAL_SCORE_MAX_MEAN_ABS_SHIFT_PTS", "net mean |shift| (pts)"),
    ("band_cross_net_pct", "LLM_EVAL_SCORE_MAX_BAND_CROSS_PCT", "net band crossings (%)"),
    ("buy_entry_cross_net_pct", "LLM_EVAL_SCORE_MAX_BUY_ENTRY_CROSS_PCT",
     "net upward crossings into Buy (%)"),
    ("self_spread", "LLM_EVAL_SCORE_MAX_SELF_SPREAD_PTS", "candidate run-to-run spread, mean |A - B| (pts)"),
    ("cumulative_shift_abs", "LLM_EVAL_SCORE_MAX_CUMULATIVE_SHIFT_PTS",
     "cumulative signed shift across switches (pts)"),
    ("failure_pct", "LLM_EVAL_MAX_FAILURE_PCT", "candidate call failures (%)"),
)
FILTER_CHECKS = (
    ("loosen_net_pct", "LLM_EVAL_FILTER_MAX_LOOSEN_PCT", "net headlines with a less severe tier (%)"),
    ("critical_release_net_pct", "LLM_EVAL_FILTER_MAX_CRITICAL_RELEASE_PCT",
     "net critical headlines released above the warn line (%)"),
    ("failure_pct", "LLM_EVAL_MAX_FAILURE_PCT", "candidate items with no valid score after a retry (%)"),
)
TRIGGER_CHECKS = (
    ("invented", "LLM_EVAL_TRIGGER_MAX_INVENTED", "invented prices"),
    ("wrong_direction", "LLM_EVAL_TRIGGER_MAX_WRONG_DIRECTION", "wrong directions"),
    ("fixture_wrong_price", "LLM_EVAL_TRIGGER_MAX_WRONG_PRICE", "fixture wrong prices"),
    ("fixture_misses", "LLM_EVAL_TRIGGER_MAX_MISSES", "fixture misses"),
    ("real_other", "LLM_EVAL_TRIGGER_MAX_REAL_DISAGREE", "other real-trade disagreements"),
    ("failure_pct", "LLM_EVAL_MAX_FAILURE_PCT", "extraction failures (%)"),
)


def limit_table(role: str) -> list[tuple[str, str, float, str]]:
    """[(metric key, constant name, current limit, label)] for the report."""
    checks = {llm.ROLE_SCORE: SCORE_CHECKS, llm.ROLE_FILTER: FILTER_CHECKS,
              llm.ROLE_EXTRACT_TRIGGER: TRIGGER_CHECKS}[role]
    return [(k, c, getattr(C, c), lbl) for k, c, lbl in checks]


def _fmt(x) -> str:
    return repr(round(x, 6)) if isinstance(x, float) else str(x)


def _failures(metrics: Mapping, checks) -> list[str]:
    out = []
    for key, const, label in checks:
        limit = getattr(C, const)
        v = metrics.get(key)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            out.append(f"{label} could not be measured, so {const} {_fmt(limit)} can't be confirmed.")
        elif v > limit:
            out.append(f"{label} is {_fmt(v)}, above {const} {_fmt(limit)}.")
    return out


def score_failures(metrics: Mapping) -> list[str]:
    out = []
    n = metrics.get("sample_n", 0)
    if n < C.LLM_EVAL_SCORE_MIN_TICKER_DAYS:
        out.append(f"sample is {n} ticker-days, below LLM_EVAL_SCORE_MIN_TICKER_DAYS "
                   f"{C.LLM_EVAL_SCORE_MIN_TICKER_DAYS}: no verdict.")
    if metrics.get("history_rows", 0) <= 0:
        out.append("no sampled row had a score_history composite, so band crossings "
                   "could not be measured.")
        return out + _failures({k: v for k, v in metrics.items()
                                if k not in ("band_cross_net_pct", "buy_entry_cross_net_pct")},
                               [c for c in SCORE_CHECKS
                                if c[0] not in ("band_cross_net_pct", "buy_entry_cross_net_pct")])
    return out + _failures(metrics, SCORE_CHECKS)


def filter_failures(metrics: Mapping) -> list[str]:
    out = []
    # The minimum counts headlines actually COMPARED (scored in every run), not
    # those sampled: an excluded headline contributes nothing to the tier checks.
    n = metrics.get("compared_n", metrics.get("sample_n", 0))
    if n < C.LLM_EVAL_FILTER_MIN_HEADLINES:
        out.append(f"only {n} negative headlines were scored in every run, below "
                   f"LLM_EVAL_FILTER_MIN_HEADLINES {C.LLM_EVAL_FILTER_MIN_HEADLINES}: no verdict.")
    return out + _failures(metrics, FILTER_CHECKS)


def trigger_failures(metrics: Mapping) -> list[str]:
    out = []
    if metrics.get("fixture_n", 0) <= 0:
        out.append("the labelled fixture is empty: no verdict.")
    return out + _failures(metrics, TRIGGER_CHECKS)


# --- small helpers ----------------------------------------------------------------
def _pmap(fn: Callable, items: list, workers: int) -> list:
    if workers <= 1 or len(items) <= 1:
        return [fn(i) for i in items]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, items))


def _mean(xs) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def _finite_num(x) -> bool:
    return (isinstance(x, (int, float)) and not isinstance(x, bool)
            and math.isfinite(float(x)))


def sentiment_points(avg: float) -> float:
    """Sentiment pillar 0-100 from an average compound, UNROUNDED
    (sentiment.sentiment_score_0_100 rounds to 0.1; unrounded keeps the
    |Δcomposite| <= 5.0 bound exact)."""
    return (float(avg) + 1.0) / 2.0 * 100.0


def delta_composite(avg_new: float, avg_old: float) -> float:
    """Composite points moved by a sentiment-average change: the sentiment weight
    times the change in the sentiment pillar (scoring.combined_score is linear)."""
    return C.COMPOSITE_WEIGHTS["sentiment"] * (sentiment_points(avg_new) - sentiment_points(avg_old))


def band(composite: float) -> str:
    """Production banding (scoring.recommendation) of a composite."""
    return recommendation(composite)["label"]


def news_tier(compound: float) -> int:
    """Alert tier of a compound (constants.NEWS_SENTIMENT_* lines)."""
    if compound <= C.NEWS_SENTIMENT_CRITICAL:
        return TIER_CRITICAL
    if compound <= C.NEWS_SENTIMENT_NEGATIVE:
        return TIER_NEGATIVE
    if compound <= C.NEWS_SENTIMENT_WARN:
        return TIER_WARN
    return TIER_CLEAR


def vader_headlines(row_headlines) -> list[dict]:
    """Rebuild the VADER-annotated input production gave the LLM: the stored
    headline texts re-run through `sentiment.analyze_news` (same VADER call, same
    {headline, score, label, url} shape, same 10-item cap)."""
    items = []
    for h in row_headlines or []:
        if not isinstance(h, Mapping):
            continue
        text = h.get("headline")
        if text:
            items.append({"title": text, "link": h.get("url") or ""})
    return analyze_news(items)[1]


def _valid_rescore(res, n: int) -> bool:
    """A usable rescore: a non-empty list of items each with a finite numeric
    score. `rescore_headlines_llm` returns a full-length copy and keeps VADER for
    headlines the model skipped, so an incomplete array is invisible here (it
    shows up in the shift metrics instead)."""
    return (isinstance(res, list) and len(res) > 0
            and all(isinstance(h, Mapping) and _finite_num(h.get("score")) for h in res))


def _avg_of(res) -> float:
    return round(sum(float(h["score"]) for h in res) / len(res), 3)


# --- score ---------------------------------------------------------------------------
def eval_score(rows, history, rescore, *, current, candidate,
               cumulative_shift_pts: float = 0.0, workers: int = 1) -> EvalResult:
    """Δcomposite of `candidate` vs the stored cache rows, net of the current model's
    own re-run (noise floor). See the module docstring for the callables."""
    cur_set = {llm.ROLE_SCORE: current}
    cand_set = {llm.ROLE_SCORE: candidate}
    hist = {(str(t).upper(), str(d)[:10]): v for (t, d), v in (history or {}).items()}

    def one(row):
        ticker = str(row.get("ticker") or "").upper()
        try:
            stored_avg = float(row.get("avg_sent"))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(stored_avg):
            return None
        hl = vader_headlines(row.get("headlines"))
        if not hl:
            return None          # production never calls the LLM with no headlines

        def run(setting):
            try:
                res = rescore([dict(h) for h in hl], ticker, setting)
            except Exception:    # noqa: BLE001 - a raising call is a failed call
                return None
            return _avg_of(res) if _valid_rescore(res, len(hl)) else None

        a, b = run(cand_set), run(cand_set)
        c = run(cur_set)
        return {
            "key": (ticker, str(row.get("score_date"))[:10]),
            "stored": stored_avg, "a": a, "b": b, "c": c,
        }

    results = [r for r in _pmap(one, list(rows or []), workers) if r is not None]
    cand_calls = 2 * len(results)
    cand_fail = sum((r["a"] is None) + (r["b"] is None) for r in results)
    cur_fail = sum(r["c"] is None for r in results)
    ok = [r for r in results if None not in (r["a"], r["b"], r["c"])]
    n = len(ok)

    da = [delta_composite(r["a"], r["stored"]) for r in ok]
    dc = [delta_composite(r["c"], r["stored"]) for r in ok]
    spread = [abs(delta_composite(r["a"], r["stored"]) - delta_composite(r["b"], r["stored"]))
              for r in ok]

    # Band crossings: only rows with a stored composite for that ticker-day.
    a_cross = c_cross = a_up = c_up = n_hist = 0
    for r, x, y in zip(ok, da, dc):
        h = hist.get(r["key"])
        if not _finite_num(h):
            continue
        n_hist += 1
        h = float(h)
        base_band = band(h)
        for d, which in ((x, "a"), (y, "c")):
            new = round(h + d, 1)
            crossed = band(new) != base_band
            up = h < C.COMPOSITE_BUY <= new
            if which == "a":
                a_cross += crossed
                a_up += up
            else:
                c_cross += crossed
                c_up += up

    def pct(count, denom):
        return count * 100.0 / denom if denom else 0.0

    mean_a, mean_c = _mean(da), _mean(dc)
    abs_a, abs_c = _mean(abs(x) for x in da), _mean(abs(x) for x in dc)
    abs_a_vs_c = _mean(abs(x - y) for x, y in zip(da, dc))
    net_shift = mean_a - mean_c
    metrics = {
        "sample_n": n,
        "rows_considered": len(results),
        "history_rows": n_hist,
        "mean_shift_candidate": mean_a, "mean_shift_current": mean_c,
        "mean_shift_net": net_shift,
        "mean_shift_net_abs": abs(net_shift),
        "mean_abs_candidate": abs_a, "mean_abs_current": abs_c,
        "mean_abs_net": abs_a - abs_c,
        "mean_abs_candidate_vs_current": abs_a_vs_c,   # mean |A - C|, information only
        "band_cross_candidate_pct": pct(a_cross, n_hist),
        "band_cross_current_pct": pct(c_cross, n_hist),
        "band_cross_net_pct": pct(a_cross - c_cross, n_hist),
        "buy_entry_cross_candidate_pct": pct(a_up, n_hist),
        "buy_entry_cross_current_pct": pct(c_up, n_hist),
        "buy_entry_cross_net_pct": pct(a_up - c_up, n_hist),
        "self_spread": _mean(spread),
        "candidate_calls": cand_calls, "candidate_failures": cand_fail,
        "failure_pct": pct(cand_fail, cand_calls) if cand_calls else 100.0,
        "current_failures": cur_fail,
        "cumulative_prior_pts": float(cumulative_shift_pts or 0.0),
        "cumulative_shift_abs": abs(float(cumulative_shift_pts or 0.0) + net_shift),
    }
    failures = score_failures(metrics)
    return EvalResult(
        role=llm.ROLE_SCORE, model=candidate.model, effort=candidate.effort,
        capabilities_hash=capabilities_hash(candidate.capabilities),
        criteria_version=C.LLM_EVAL_CRITERIA_VERSION, baseline=BASELINE_SCORE,
        sample_n=n, metrics=metrics, passed=not failures, failures=failures)


# --- filter ---------------------------------------------------------------------------
def select_negative_headlines(headlines) -> list[dict]:
    """Items shaped for `llm_score_batch` ({ticker, title, compound, label}) for every
    distinct headline whose recomputed VADER is <= NEWS_SENTIMENT_WARN (the same
    `_is_neg` line production uses). `headlines`: dicts with "title" (or
    "headline") and an optional "ticker". De-duplicated on production's memo key."""
    out, seen = [], set()
    for h in headlines or []:
        if isinstance(h, str):
            h = {"title": h}
        title = h.get("title") or h.get("headline")
        if not title:
            continue
        hl = analyze_news([{"title": title}])[1]
        if not hl:
            continue
        item = {"ticker": str(h.get("ticker") or "").upper(), "title": title,
                "compound": hl[0]["score"], "label": hl[0]["label"]}
        k = _memo_key(item)
        if k in seen or item["compound"] > C.NEWS_SENTIMENT_WARN:
            continue
        seen.add(k)
        out.append(item)
    return out


def _filter_run(items, score_batch, setting, workers):
    """One production-shaped pass: batches of `_RESCORE_BATCH`, suppress-only applied
    with production's `_apply_suppress_only`. A failed batch (raise, non-list,
    wrong length) is retried ONCE (eval-only; production does not retry). Returns
    a list of (final compound, failed) per item: an item fails when it has no valid
    score after the retry, and then stays at its VADER compound."""
    batches = [items[i:i + _RESCORE_BATCH] for i in range(0, len(items), _RESCORE_BATCH)]

    def attempt(b):
        try:
            got = score_batch([dict(i) for i in b], setting)
        except Exception:    # noqa: BLE001
            return None
        return got if isinstance(got, list) and len(got) == len(b) else None

    def call(b):
        got = attempt(b)
        return got if got is not None else attempt(b)

    outs = _pmap(call, batches, workers)
    finals = []
    for b, got in zip(batches, outs):
        scores = got if got is not None else [None] * len(b)
        for item, sc in zip(b, scores):
            upd = _apply_suppress_only(item, sc) if sc is not None else None
            finals.append(((upd or item)["compound"], sc is None))
    return finals


def eval_filter(headlines, score_batch, *, current, candidate, workers: int = 1) -> EvalResult:
    """Alert-tier changes of `candidate` vs the current model's live re-run, net of
    the current model's run-to-run noise (C1 vs C2). Runs: current twice, candidate
    once. Items that failed in ANY run are excluded from the tier comparisons (and
    counted). Tier percentages are over the COMPARED items (excluded ones can't
    loosen, so counting them would dilute the rate); `failure_pct` is the
    candidate's failed items / all sampled items."""
    items = select_negative_headlines(headlines)
    n = len(items)
    cur_set = {llm.ROLE_FILTER: current}
    cand_set = {llm.ROLE_FILTER: candidate}
    r1 = _filter_run(items, score_batch, cur_set, workers)
    r2 = _filter_run(items, score_batch, cur_set, workers)
    rk = _filter_run(items, score_batch, cand_set, workers)
    keep = [i for i in range(n) if not (r1[i][1] or r2[i][1] or rk[i][1])]
    c1 = [r1[i][0] for i in keep]
    c2 = [r2[i][0] for i in keep]
    k = [rk[i][0] for i in keep]

    def vs(base, other):
        loosen = tighten = release = 0
        for b, o in zip(base, other):
            tb, to = news_tier(b), news_tier(o)
            loosen += to < tb
            tighten += to > tb
            release += tb == TIER_CRITICAL and o > C.NEWS_SENTIMENT_WARN
        return loosen, tighten, release

    kl, kt, kr = vs(c1, k)
    cl, ct, cr = vs(c1, c2)

    m = len(keep)

    def pct(count):                 # tier changes: over the compared headlines
        return count * 100.0 / m if m else 0.0

    def fpct(count):                # failures: over all sampled headlines
        return count * 100.0 / n if n else 0.0

    f1, f2, fk = (sum(1 for x in r if x[1]) for r in (r1, r2, rk))
    metrics = {
        "sample_n": n,
        "compared_n": len(keep), "excluded_n": n - len(keep),
        "critical_n": sum(news_tier(x) == TIER_CRITICAL for x in c1),
        "tier_counts_current": {_TIER_NAMES[t]: sum(news_tier(x) == t for x in c1)
                                for t in _TIER_NAMES},
        "loosen_candidate_pct": pct(kl), "loosen_noise_pct": pct(cl),
        "loosen_net_pct": pct(kl - cl),
        "tighten_candidate_pct": pct(kt), "tighten_noise_pct": pct(ct),
        "tighten_net_pct": pct(kt - ct),
        "critical_release_candidate_pct": pct(kr), "critical_release_noise_pct": pct(cr),
        "critical_release_net_pct": pct(kr - cr),
        "failed_items_c1": f1, "failed_items_c2": f2, "failed_items_candidate": fk,
        "failure_pct_c1": fpct(f1), "failure_pct_c2": fpct(f2), "failure_pct_candidate": fpct(fk),
        "candidate_failures": fk,
        "failure_pct": fpct(fk) if n else 100.0,
    }
    failures = filter_failures(metrics)
    return EvalResult(
        role=llm.ROLE_FILTER, model=candidate.model, effort=candidate.effort,
        capabilities_hash=capabilities_hash(candidate.capabilities),
        criteria_version=C.LLM_EVAL_CRITERIA_VERSION, baseline=BASELINE_FILTER,
        sample_n=n, metrics=metrics, passed=not failures, failures=failures)


# --- extract_trigger --------------------------------------------------------------------
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

CLS_OK = "ok"
CLS_INVENTED = "invented"
CLS_WRONG_DIRECTION = "wrong_direction"
CLS_WRONG_PRICE = "wrong_price"
CLS_MISSED = "missed"
CLS_PARSE_FAILURE = "parse_failure"
# Real trades only: a price that IS in the text where none was stored. The stored
# trigger is the old model's output, not ground truth, so this is a listed
# disagreement (LLM_EVAL_TRIGGER_MAX_REAL_DISAGREE), not an invented price.
CLS_NEW_PRICE = "new_price"


def numbers_in(text: str) -> list[float]:
    """Every numeral in `text` (commas stripped)."""
    out = []
    for m in _NUM_RE.findall(text or ""):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def classify_trigger(text: str, expected_price, expected_direction, got, *,
                     expected_is_truth: bool = True) -> str:
    """Classify one extraction against its expected (fixture label / stored) trigger.

    `got` is production's parsed result ({"checkable", "direction", "price_level"})
    or None. Priority: parse failure; invented (a price that appears nowhere in the
    text, or, when `expected_is_truth`, any price where none is expected); wrong
    direction; wrong price; missed. With `expected_is_truth=False` (real trades: the
    stored trigger is the old model's output) a price found in the text where none
    was stored is CLS_NEW_PRICE."""
    has_expected = _finite_num(expected_price) and expected_direction in ("above", "below")
    if (not isinstance(got, Mapping) or not isinstance(got.get("checkable"), bool)):
        return CLS_PARSE_FAILURE
    if not got["checkable"]:
        return CLS_MISSED if has_expected else CLS_OK
    price = got.get("price_level")
    if not _finite_num(price) or got.get("direction") not in ("above", "below"):
        return CLS_PARSE_FAILURE
    price = float(price)
    tol = C.LLM_EVAL_TRIGGER_PRICE_TOL
    in_text = any(abs(n - price) <= tol for n in numbers_in(text))
    if not has_expected:
        return CLS_NEW_PRICE if (in_text and not expected_is_truth) else CLS_INVENTED
    if not in_text:
        return CLS_INVENTED
    if got["direction"] != expected_direction:
        return CLS_WRONG_DIRECTION
    if abs(price - float(expected_price)) > tol:
        return CLS_WRONG_PRICE
    return CLS_OK


def eval_trigger(fixture, real_rows, extract, *, candidate, workers: int = 1) -> EvalResult:
    """Pre-Mortem trigger extraction by `candidate`: every fixture case and real trade
    run twice. `fixture`: [{id, text, expected: {price, direction}}]. `real_rows`:
    [{id, premortem_commitment, premortem_trigger_price, premortem_trigger_direction}]
    (db.load_premortem_trigger_rows). `extract(text, setting)` -> production's parsed
    trigger or None. Counts are over BOTH runs."""
    setting = {llm.ROLE_EXTRACT_TRIGGER: candidate}
    jobs = []
    for case in fixture or []:
        exp = case.get("expected")
        if not isinstance(exp, Mapping):
            exp = {}
        jobs.append(("fixture", case.get("id"), case.get("text") or "",
                     exp.get("price"), exp.get("direction")))
    for r in real_rows or []:
        # Stored direction has three states: NULL = never extracted (UNLABELLED:
        # many trades predate the trigger columns, and a failed extraction also
        # stays NULL), "not_checkable" = the old model said no price, and
        # "below" / "above" = a stored trigger.
        jobs.append(("real", r.get("id"), r.get("premortem_commitment") or "",
                     r.get("premortem_trigger_price"), r.get("premortem_trigger_direction")))

    def run(job):
        kind, _, text, ep, ed = job
        outs = []
        for _run in (1, 2):
            try:
                got = extract(text, setting)
            except Exception:    # noqa: BLE001
                got = None
            outs.append((classify_trigger(text, ep, ed, got,
                                         expected_is_truth=(kind == "fixture")), got))
        return outs

    def unlabelled(kind, ed):
        return kind == "real" and ed not in ("not_checkable", "below", "above")

    outcomes = _pmap(run, jobs, workers)
    counts = {CLS_INVENTED: 0, CLS_WRONG_DIRECTION: 0, CLS_WRONG_PRICE: 0,
              CLS_MISSED: 0, CLS_PARSE_FAILURE: 0}
    fixture_wrong_price = fixture_misses = real_other = 0
    disagreements = []
    for job, outs in zip(jobs, outcomes):
        kind, jid, _text, ep, ed = job
        is_unlabelled = unlabelled(kind, ed)
        for run_no, (cls, got) in enumerate(outs, start=1):
            if is_unlabelled:
                # No stored label: only a price that appears nowhere in the text
                # and a parse failure count; the rest is listed for information.
                if cls in (CLS_INVENTED, CLS_PARSE_FAILURE):
                    counts[cls] += 1
                if cls != CLS_OK:
                    disagreements.append({
                        "trade_id": jid, "run": run_no, "class": cls,
                        "stored": {"price": ep, "direction": ed},
                        "candidate": (dict(got) if isinstance(got, Mapping) else None),
                        "unlabelled": True,
                    })
                continue
            if cls in counts:
                counts[cls] += 1
            if kind == "fixture":
                fixture_wrong_price += cls == CLS_WRONG_PRICE
                fixture_misses += cls == CLS_MISSED
            elif cls != CLS_OK:
                if cls in (CLS_WRONG_PRICE, CLS_MISSED, CLS_NEW_PRICE):
                    real_other += 1
                disagreements.append({
                    "trade_id": jid, "run": run_no, "class": cls,
                    "stored": {"price": ep, "direction": ed},
                    "candidate": (dict(got) if isinstance(got, Mapping) else None),
                })
    calls = 2 * len(jobs)
    fixture_n = sum(1 for j in jobs if j[0] == "fixture")
    metrics = {
        "sample_n": len(jobs), "fixture_n": fixture_n, "real_n": len(jobs) - fixture_n,
        "invented": counts[CLS_INVENTED],
        "wrong_direction": counts[CLS_WRONG_DIRECTION],
        "wrong_price": counts[CLS_WRONG_PRICE],
        "misses": counts[CLS_MISSED],
        "parse_failures": counts[CLS_PARSE_FAILURE],
        "fixture_wrong_price": fixture_wrong_price,
        "fixture_misses": fixture_misses,
        "real_other": real_other,
        "failure_pct": (counts[CLS_PARSE_FAILURE] * 100.0 / calls) if calls else 100.0,
        "real_disagreements": disagreements,
    }
    failures = trigger_failures(metrics)
    return EvalResult(
        role=llm.ROLE_EXTRACT_TRIGGER, model=candidate.model, effort=candidate.effort,
        capabilities_hash=capabilities_hash(candidate.capabilities),
        criteria_version=C.LLM_EVAL_CRITERIA_VERSION, baseline=BASELINE_TRIGGER,
        sample_n=len(jobs), metrics=metrics, passed=not failures, failures=failures)
