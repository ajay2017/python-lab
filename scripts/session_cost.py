"""Weekly Claude Code session cost vs commits shipped, against a weekly budget.

Reads every Claude Code transcript for this repo (main sessions and
subagents) from ~/.claude/projects/<project>/, prices each assistant
message's token usage at Anthropic API list rates, and joins the result to
`git log` per ISO week. The output is an API-EQUIVALENT cost: a subscription
plan is not billed per token, so use this as the yardstick and calibrate it
against the plan's weekly limit (`/usage` in Claude Code) at the end of a week.

Usage:
  python scripts/session_cost.py                 # $250/week budget
  python scripts/session_cost.py --budget 300
  python scripts/session_cost.py --transcripts <dir>   # if auto-detect fails

Read-only: it never writes anything. Weeks are ISO weeks in UTC.
"""
import argparse
import glob
import json
import os
import re
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# $/MTok: (input, output, cache read). Cache write = 1.25x input (5-minute
# TTL) or 2x input (1-hour TTL). Source: Anthropic API price list, cached
# 2026-10-06. More specific keys first: "opus-5-5" must match before "opus-5".
PRICE = {
    "opus-5-5": (4.0, 20.0, 0.20),
    "opus-5": (5.0, 25.0, 0.50),
    "opus-4": (5.0, 25.0, 0.50),
    "fable": (10.0, 50.0, 0.25),
    "sonnet-5": (2.0, 10.0, 0.20),
    "sonnet-4": (3.0, 15.0, 0.30),
    "haiku-5": (0.10, 0.50, 0.01),
    "haiku-4": (1.0, 5.0, 0.10),
}

# Trailing complete weeks used for the forecast.
FORECAST_WEEKS = 4


def price_key(model):
    for key in PRICE:
        if key in (model or ""):
            return key
    return None


def default_transcripts_dir():
    # Claude Code names the folder after the repo path with every
    # non-alphanumeric character replaced by "-".
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(ROOT))
    base = Path.home() / ".claude" / "projects"
    for cand in (slug, slug[:1].lower() + slug[1:], slug[:1].upper() + slug[1:]):
        if (base / cand).is_dir():
            return base / cand
    return base / slug


def message_cost(usage, key):
    pin, pout, pread = PRICE[key]
    cc = usage.get("cache_creation") or {}
    w1h = cc.get("ephemeral_1h_input_tokens")
    w5m = cc.get("ephemeral_5m_input_tokens")
    if w1h is None and w5m is None:
        # No TTL split recorded: price as 1h, the dearer of the two.
        w1h, w5m = usage.get("cache_creation_input_tokens", 0) or 0, 0
    return (
        (usage.get("input_tokens", 0) or 0) * pin
        + (usage.get("output_tokens", 0) or 0) * pout
        + (usage.get("cache_read_input_tokens", 0) or 0) * pread
        + (w5m or 0) * pin * 1.25
        + (w1h or 0) * pin * 2.0
    ) / 1e6


def iso_week(dt):
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def load_costs(transcripts):
    seen = set()
    week_cost = defaultdict(float)
    week_model = defaultdict(lambda: defaultdict(float))
    week_sessions = defaultdict(set)
    cache_read_cost = 0.0
    unpriced = defaultdict(int)
    for path in glob.glob(os.path.join(transcripts, "**", "*.jsonl"), recursive=True):
        is_sub = os.sep + "subagents" + os.sep in path
        session = (os.path.basename(os.path.dirname(os.path.dirname(path)))
                   if is_sub else os.path.basename(path)[:-6])
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"usage"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                msg = d.get("message") or {}
                usage, mid, ts = msg.get("usage"), msg.get("id"), d.get("timestamp")
                # A streamed message repeats its usage on several lines.
                if not usage or not mid or not ts or mid in seen:
                    continue
                seen.add(mid)
                key = price_key(msg.get("model"))
                if key is None:
                    unpriced[msg.get("model")] += 1
                    continue
                wk = iso_week(datetime.fromisoformat(ts.replace("Z", "+00:00")))
                cost = message_cost(usage, key)
                week_cost[wk] += cost
                week_model[wk][key] += cost
                week_sessions[wk].add(session)
                cache_read_cost += (usage.get("cache_read_input_tokens", 0) or 0) * PRICE[key][2] / 1e6
    return week_cost, week_model, week_sessions, cache_read_cost, unpriced


def load_commits(since):
    log = subprocess.run(
        ["git", "-C", str(ROOT), "log", f"--since={since}", "--format=%cI|%s"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    week_commits = defaultdict(lambda: defaultdict(int))
    for row in log.splitlines():
        ts, _, subj = row.partition("|")
        m = re.match(r"(\w+)[(!:]", subj)
        week_commits[iso_week(datetime.fromisoformat(ts))][m.group(1) if m else "other"] += 1
    return week_commits


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--budget", type=float, default=250.0, help="weekly budget in API-equivalent dollars")
    ap.add_argument("--transcripts", help="Claude Code transcripts folder for this repo")
    args = ap.parse_args()

    transcripts = Path(args.transcripts) if args.transcripts else default_transcripts_dir()
    if not transcripts.is_dir():
        raise SystemExit(f"No transcripts folder at {transcripts} - pass --transcripts")

    week_cost, week_model, week_sessions, cache_read_cost, unpriced = load_costs(str(transcripts))
    if not week_cost:
        raise SystemExit(f"No priced messages found under {transcripts}")
    weeks = sorted(week_cost)
    commits = load_commits(weeks[0][:4] + "-01-01")
    this_week = iso_week(datetime.now().astimezone())

    print(f"{'week':9} {'cost $':>8} {'vs budget':>9} {'sess':>4} {'commits':>7} "
          f"{'feat':>4} {'fix':>4} {'$/commit':>8}  by model")
    for wk in weeks:
        c, wc = week_cost[wk], commits.get(wk, {})
        n = sum(wc.values())
        models = ", ".join(f"{m} {v:.0f}" for m, v in
                           sorted(week_model[wk].items(), key=lambda x: -x[1]) if v >= 1)
        flag = " (in progress)" if wk == this_week else ""
        print(f"{wk:9} {c:8.2f} {c / args.budget:8.0%} {len(week_sessions[wk]):4d} {n:7d} "
              f"{wc.get('feat', 0):4d} {wc.get('fix', 0):4d} {(c / n if n else 0):8.2f}  {models}{flag}")

    total = sum(week_cost.values())
    print(f"\nTotal ${total:,.2f} since {weeks[0]}; "
          f"{cache_read_cost / total:.0%} of it is re-reading cached context.")

    done = [w for w in weeks if w != this_week][-FORECAST_WEEKS:]
    spend = sum(week_cost[w] for w in done)
    n_commits = sum(sum(commits.get(w, {}).values()) for w in done)
    print(f"\nForecast (last {len(done)} complete weeks: {done[0]} to {done[-1]}):")
    print(f"  average spend      ${spend / len(done):,.0f}/week "
          f"({spend / len(done) / args.budget:.1f}x the ${args.budget:,.0f} budget)")
    if n_commits:
        per_commit = spend / n_commits
        print(f"  cost per commit    ${per_commit:.2f}")
        print(f"  budget buys        ~{args.budget / per_commit:.0f} commits/week")

    if this_week in week_cost:
        used = week_cost[this_week]
        print(f"\nThis week ({this_week}): ${used:,.2f} used, "
              f"${max(args.budget - used, 0):,.2f} left of ${args.budget:,.0f}.")
    if unpriced:
        print(f"\nSkipped messages with no price: {dict(unpriced)}")


if __name__ == "__main__":
    main()
