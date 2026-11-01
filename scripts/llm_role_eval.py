#!/usr/bin/env python3
"""Run the decision-role eval (docs/plans/llm-model-routing.md section 17.3).

Chunk C2. App Settings may switch `score` / `filter` / `extract_trigger` to a new
model only after a stored PASSING eval of that exact model + effort. This script
runs that eval and, unless --no-save, stores one `llm_role_evals` row.

Side effects: reads `sentiment_llm_cache`, `score_history`, `trades`; makes real
Anthropic calls (score: about 3 per sampled row; filter: 3 passes over the
negative headlines in batches of 25; extract_trigger: 2 per fixture case / trade);
writes ONLY one `llm_role_evals` row (never the score cache, trades or
reference_tables). Needs SUPABASE_URL, SUPABASE_KEY and an Anthropic key in the
environment (run from a normal shell). Running with --model equal to the current
model is allowed: that is the engine check.

Usage:
    python scripts/llm_role_eval.py --role score --model claude-haiku-5-5
    python scripts/llm_role_eval.py --role filter --model claude-haiku-5-5 --no-save
    python scripts/llm_role_eval.py --role extract_trigger --model claude-haiku-5-5 --max-rows 5
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_analyzer import constants as C  # noqa: E402
from stock_analyzer import llm, llm_role_eval as ev, sentiment_cache  # noqa: E402

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "premortem_trigger_cases.json"
WORKERS = 3   # production's own rescore pool is 3 (news_intelligence._RESCORE_WORKERS)


def _die(msg: str, code: int = 2):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def _code_sha() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, timeout=10,
                             cwd=str(Path(__file__).resolve().parent.parent))
        sha = out.stdout.decode("utf-8", "replace").strip()
        return sha if out.returncode == 0 and sha else None
    except Exception:  # noqa: BLE001
        return None


def _current_setting(role: str) -> llm.RoleSetting:
    """What production resolves for `role` now."""
    try:
        from stock_analyzer import llm_decision_settings as lds  # C4's module: lazy
        rs = lds.current_setting().get(role)
        if rs is not None:
            return rs
    except Exception:  # noqa: BLE001
        pass
    return llm.resolved_setting(role) or llm.RoleSetting(model=llm.CODE_DEFAULTS[role])


def _score_cumulative() -> float:
    try:
        from stock_analyzer import llm_decision_settings as lds
        entry = lds.get_snapshot().entries.get(llm.ROLE_SCORE)
        return lds.score_cumulative_for(entry, sentiment_cache.score_date_today())
    except Exception:  # noqa: BLE001
        return 0.0


def _window() -> tuple[str, str]:
    today = datetime.now(timezone.utc).date()          # UTC: the cache's own score_date basis
    return ((today - timedelta(days=C.LLM_EVAL_SCORE_LOOKBACK_DAYS)).isoformat(),
            (today - timedelta(days=1)).isoformat())   # today excluded


def score_sample(rows, current: llm.RoleSetting) -> list:
    """The cache rows produced by ``current`` (``sentiment_cache.row_matches``: a
    NULL model reads as Haiku 4.5, not as whatever ``CODE_DEFAULTS`` says today)."""
    return [r for r in rows if sentiment_cache.row_matches(r, current)]


def _print_report(res: ev.EvalResult, role: str) -> None:
    print()
    print(f"=== {role} eval: {'PASS' if res.passed else 'FAIL'} ===")
    print(f"model {res.model}  effort {res.effort}  criteria v{res.criteria_version}  "
          f"sample_n {res.sample_n}  baseline: {res.baseline}")
    print("capabilities hash:", res.capabilities_hash[:16], "...")
    print()
    print(f"{'metric':44} {'value':>10}   {'limit':>8}  constant")
    for key, const, limit, label in ev.limit_table(role):
        v = res.metrics.get(key)
        print(f"{label:44} {ev._fmt(v):>10}   {ev._fmt(limit):>8}  {const}")
    print()
    extras = {k: v for k, v in res.metrics.items()
              if k not in {k2 for k2, *_ in ev.limit_table(role)} and k != "real_disagreements"}
    for k, v in extras.items():
        print(f"  {k}: {v if not isinstance(v, float) else round(v, 4)}")
    for d in res.metrics.get("real_disagreements", []) or []:
        print(f"  real-trade disagreement: trade {d['trade_id']} run {d['run']} "
              f"{d['class']}: stored {d['stored']} vs candidate {d['candidate']}")
    if res.failures:
        print("\nFailed limits:")
        for f in res.failures:
            print("  -", f)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--role", required=True,
                    choices=[llm.ROLE_SCORE, llm.ROLE_FILTER, llm.ROLE_EXTRACT_TRIGGER])
    ap.add_argument("--model", required=True)
    ap.add_argument("--effort", default=None)
    ap.add_argument("--max-rows", type=int, default=None,
                    help="cap the sampled rows (quick try; the min-sample limit will then fail)")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args(argv)

    for var in ("SUPABASE_URL", "SUPABASE_KEY"):
        if not os.environ.get(var):
            _die(f"{var} is not set in the environment.")
    api_key = llm.resolve_anthropic_key(environ=os.environ)
    if not api_key:
        _die("no Anthropic key found (ANTHROPIC_API_KEY).")

    if args.effort and args.effort not in llm.ALLOWED_EFFORTS:
        _die(f"effort {args.effort!r} is not allowed (allowed: "
             f"{list(llm.ALLOWED_EFFORTS)}).")
    caps, err, _kind = llm.retrieve_model(api_key, args.model)
    if caps is None:
        _die(f"couldn't retrieve model {args.model!r} from the Models API: {err}")
    if args.effort and args.effort not in (caps.get("efforts") or []):
        _die(f"model {args.model!r} doesn't list effort {args.effort!r} "
             f"(supports {caps.get('efforts')}).")
    candidate = llm.RoleSetting(model=args.model, effort=args.effort, capabilities=caps)
    current = _current_setting(args.role)
    print(f"role {args.role}: current {current.model} (effort {current.effort}) -> "
          f"candidate {candidate.model} (effort {candidate.effort})")

    from stock_analyzer import db
    from stock_analyzer import news_intelligence as ni, premortem_monitor as pm

    if args.role == llm.ROLE_EXTRACT_TRIGGER:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        real = db.load_premortem_trigger_rows()
        if real is None:
            _die("couldn't read trades (premortem rows).", 1)
        if args.max_rows:
            fixture, real = fixture[:args.max_rows], real[:args.max_rows]
        print(f"fixture cases {len(fixture)}, real trades with a commitment {len(real)}")
        res = ev.eval_trigger(
            fixture, real,
            lambda text, setting: pm.extract_trigger(text, api_key, setting=setting),
            candidate=candidate, workers=WORKERS)
    else:
        since, until = _window()
        rows = db.load_sentiment_cache_sample(since, until)
        if rows is None:
            _die("couldn't read sentiment_llm_cache.", 1)
        print(f"cache rows {since}..{until}: {len(rows)}")
        if args.role == llm.ROLE_SCORE:
            rows = score_sample(rows, current)
            print(f"rows produced by the current entry ({current.model}, {current.effort}): {len(rows)}")
            if args.max_rows:
                rows = rows[:args.max_rows]
            history = db.load_score_history_composites(since, until)
            if history is None:
                _die("couldn't read score_history.", 1)
            res = ev.eval_score(
                rows, history,
                lambda hl, ticker, setting: ni.rescore_headlines_llm(
                    hl, api_key, ticker, setting=setting),
                current=current, candidate=candidate,
                cumulative_shift_pts=_score_cumulative(), workers=WORKERS)
        else:
            if args.max_rows:
                rows = rows[:args.max_rows]
            heads = [{"ticker": r.get("ticker"), "title": h.get("headline")}
                     for r in rows for h in (r.get("headlines") or [])
                     if isinstance(h, dict)]
            res = ev.eval_filter(
                heads,
                lambda items, setting: ni.llm_score_batch(items, api_key, setting=setting),
                current=current, candidate=candidate, workers=WORKERS)

    _print_report(res, args.role)
    if args.no_save:
        print("\n--no-save: nothing stored.")
    else:
        eval_id = db.save_llm_role_eval(res.to_row(ran_by="script", code_sha=_code_sha()))
        if eval_id is None:
            print("\nWARNING: the eval row was NOT saved (DDL missing, read-only or DB error).")
            return 1
        print(f"\nSaved llm_role_evals id {eval_id}.")
    return 0 if res.passed else 1


if __name__ == "__main__":
    sys.exit(main())
