#!/usr/bin/env python3
"""Sweep every read of a documented coordination cache in app.py, by page.

Chunk 1 of F-260 (surface proprioception). This is the MEASUREMENT step
CLAUDE.md's "33 offline-sentinel-collapsing cache reads" queue item asks for —
deliberately read-only: it inventories and attributes, it never edits and never
judges. The judgment (which reads are claim-bearing) is hand-classified into
`stock_analyzer/surface_trust.py`; this script exists so that classification is
made against a complete, reproducible list rather than a sample.

WHY IT EXISTS AT ALL. Two enumerations of this same class have already been
wrong in a way that mattered:

  * the 2026-08-26 app review reported "18 sites across 8 pages", which was a
    sample, not a sweep;
  * CLAUDE.md's own corrected figure said 33 while its per-page breakdown summed
    to 29 (a dropped page name — 📅 Economic Calendar), AND its `or {}` / `or []`
    / `.get(k, {})` filter structurally EXCLUDED the highest-severity shape in
    the set: scalar and DataFrame defaults.

That second point is the one that matters most and drives this script's design.
`st.session_state.get("_port_df_enriched", pd.DataFrame())` is the exact form
behind the F-258 defect, and
`float(st.session_state.get("_portfolio_value", 0) or 0)` hands a sizing routine
a $0 budget. Neither contains `or {}` or `or []`. **A filter defined by which
default token was typed cannot see them.** So this script reports FORM but
filters on nothing — every read of a documented key is listed, and severity is
decided by a human reading what the falsy branch renders.

Usage:
    python scripts/sweep_coordination_reads.py            # grouped summary
    python scripts/sweep_coordination_reads.py --csv      # line-per-hit, for triage
    python scripts/sweep_coordination_reads.py --keys     # the key list it uses
"""
from __future__ import annotations

import argparse
import ast
import io
import os
import re
import sys
from collections import Counter, defaultdict

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP = os.path.join(_REPO, "app.py")

# The documented coordination caches, transcribed from CLAUDE.md's
# "Coordination pattern" section. Dynamically-named keys (the
# `_rh_prices_cache_{start}_{end}` family) are matched by prefix.
_KEYS: tuple[str, ...] = (
    "_last_port_df", "_port_df_enriched", "_last_held_data", "_last_held_tickers",
    "_portfolio_value", "_port_risk_cache", "_fragility_cache", "_highbeta_share",
    "_risk_high_alerts_cache", "_risk_advisor_recs_cache", "_alert_list_cache",
    "_actions_cache", "_div_recs_cache", "_corr_df_cache", "_div_score_cache",
    "_avg_corr_cache", "_risk_pairs_cache", "_div_label_cache", "_corr_coverage_cache",
    "_grow_today_sectors_cache", "_grow_composites", "_grow_composites_coverage",
    "_daily_brief_offline", "_acct_gate_cache", "_leverage_cache", "_reduce_calls",
    "_holdings_sig_at_home_build", "_day_shock_cache", "_structural_alert_cache",
    "_dpnl_cache", "_leading_sectors_cache", "_market_tone_cache", "_mirror_orphans",
    "_mirror_overexp", "_mirror_overhangs", "_pi_factor_tilt_cache",
    "_broker_drift_cache", "_home_synth_cache",
)
_KEY_PREFIXES: tuple[str, ...] = ("_rh_prices_cache_",)

# Producer pages — a page legitimately reads its OWN key before publishing it,
# so these pairs are never findings. 📒 Trade Journal is a SECONDARY producer
# (`_refresh_portfolio_cache_after_trade`) and republishes the core portfolio
# keys mid-render.
_PRODUCERS: dict[str, tuple[str, ...]] = {
    "🏠 Home": ("*",),
    "🎯 My Edge": ("_mirror_orphans", "_mirror_overexp", "_mirror_overhangs"),
    "🧩 Intelligence": ("_pi_factor_tilt_cache",),
    "📒 Trade Journal": ("_last_port_df", "_port_df_enriched", "_last_held_data",
                         "_last_held_tickers", "_portfolio_value", "_port_risk_cache"),
}

_PAGE_RE = re.compile(r'^(?:if|elif) page == "(.+?)":')


def _is_key(name: str) -> bool:
    return name in _KEYS or any(name.startswith(p) for p in _KEY_PREFIXES)


def _page_ranges(lines: list[str]) -> list[tuple[int, str]]:
    """(start_line, page_label) for each dispatch arm, in file order."""
    out = []
    for i, line in enumerate(lines, start=1):
        m = _PAGE_RE.match(line)
        if m:
            out.append((i, m.group(1)))
    return out


def _page_for(lineno: int, ranges: list[tuple[int, str]]) -> str:
    page = "<pre-dispatch / shared>"
    for start, label in ranges:
        if lineno >= start:
            page = label
        else:
            break
    return page


def _classify_form(node: ast.Call, parent_map: dict[int, ast.AST]) -> str:
    """Describe the DEFAULT/fallback shape at this read. Never used to filter."""
    # `.get(key, <default>)`
    if len(node.args) >= 2:
        d = node.args[1]
        if isinstance(d, ast.Dict) and not d.keys:
            return "get(k, {})"
        if isinstance(d, ast.List) and not d.elts:
            return "get(k, [])"
        if isinstance(d, ast.Constant):
            return f"get(k, {d.value!r})"
        if isinstance(d, ast.Call):
            f = d.func
            nm = getattr(f, "attr", None) or getattr(f, "id", None)
            return f"get(k, {nm}())"
        return "get(k, <expr>)"
    # bare `.get(key)` — look at how the RESULT is used
    p = parent_map.get(id(node))
    if isinstance(p, ast.BoolOp) and isinstance(p.op, ast.Or):
        for v in p.values[1:]:
            if isinstance(v, ast.Dict) and not v.keys:
                return "get(k) or {}"
            if isinstance(v, ast.List) and not v.elts:
                return "get(k) or []"
            if isinstance(v, ast.Constant):
                return f"get(k) or {v.value!r}"
        return "get(k) or <expr>"
    if isinstance(p, ast.Compare):
        for c in p.comparators:
            if isinstance(c, ast.Constant) and c.value is None:
                return "get(k) is None  [GUARDED]"
    return "get(k)  [raw]"


def sweep() -> list[dict]:
    src = io.open(_APP, encoding="utf-8").read().lstrip("﻿")
    lines = src.splitlines()
    ranges = _page_ranges(lines)
    tree = ast.parse(src)

    parent_map: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parent_map[id(child)] = parent

    hits: list[dict] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "get" or not node.args:
            continue
        # receiver must be session_state (st.session_state.get / session_state.get)
        recv = node.func.value
        recv_txt = ast.unparse(recv)
        if "session_state" not in recv_txt:
            continue
        k = node.args[0]
        if not (isinstance(k, ast.Constant) and isinstance(k.value, str) and _is_key(k.value)):
            continue
        page = _page_for(node.lineno, ranges)
        owned = _PRODUCERS.get(page, ())
        is_producer = ("*" in owned) or (k.value in owned)
        hits.append({
            "line": node.lineno,
            "page": page,
            "key": k.value,
            "form": _classify_form(node, parent_map),
            "producer": is_producer,
        })
    hits.sort(key=lambda h: h["line"])
    return hits


def main() -> int:
    # Page labels are emoji; a Windows cp1252 console cannot encode them.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", action="store_true", help="one line per hit")
    ap.add_argument("--keys", action="store_true", help="print the key list and exit")
    args = ap.parse_args()

    if args.keys:
        for k in _KEYS:
            print(k)
        for p in _KEY_PREFIXES:
            print(p + "*")
        return 0

    hits = sweep()
    consumers = [h for h in hits if not h["producer"]]

    if args.csv:
        print("line,page,key,form,producer")
        for h in hits:
            print(f'{h["line"]},"{h["page"]}",{h["key"]},"{h["form"]}",{h["producer"]}')
        return 0

    print(f"Total documented-cache reads in app.py : {len(hits)}")
    print(f"  on a PRODUCER page (never a finding) : {len(hits) - len(consumers)}")
    print(f"  on a CONSUMER page (triage these)    : {len(consumers)}")
    print()
    print("By form (consumer pages only) — note NO form is filtered out:")
    for form, n in Counter(h["form"] for h in consumers).most_common():
        print(f"  {n:3d}  {form}")
    print()
    print("By page (consumer pages only):")
    by_page: dict[str, list[dict]] = defaultdict(list)
    for h in consumers:
        by_page[h["page"]].append(h)
    for page, hs in sorted(by_page.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(hs):3d}  {page}")
        for h in hs:
            print(f"         L{h['line']:<6} {h['key']:<30} {h['form']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
