"""pred_report.py — James's call scoreboard, direction AND strike frame.

Collapsing a call to "direction right/wrong" hides the trade: a right call on a
blown strike is a losing spread. This keeps both axes and reports them together.

Columns read: prediction, type, level, result, strike_pct, strike_held.
Run: python pred_report.py [days]     (default 30)
"""
import csv
import datetime
import sys

import james_pipe as jp

days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
cut = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()

rows = []
if jp.PRED_FILE.exists():
    with jp.PRED_FILE.open(encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("date") or "") >= cut]

scored = [r for r in rows if (r.get("result") or "").lower() in ("correct", "wrong")]
dir_right = sum(1 for r in scored if r["result"].lower() == "correct")
print(f"predictions, last {days} days — {len(rows)} captured, {len(scored)} scored")
if scored:
    print(f"direction hit-rate: {dir_right}/{len(scored)} "
          f"({dir_right / len(scored):.0%})")

buckets = {"already ITM (<0%)": [], "tight 0-1%": [], "1-3%": [], "roomy >3%": []}
for r in rows:
    try:
        p = float(r.get("strike_pct") or "")
    except ValueError:
        continue
    key = ("already ITM (<0%)" if p < 0 else "tight 0-1%" if p < 1
           else "1-3%" if p < 3 else "roomy >3%")
    buckets[key].append(r)

print("\nstrike room at claim -> did the short strike survive?")
for key, rs in buckets.items():
    held = [r for r in rs if (r.get("strike_held") or "").lower() == "true"]
    blown = [r for r in rs if (r.get("strike_held") or "").lower() == "false"]
    if not rs:
        continue
    decided = len(held) + len(blown)
    rate = f"{len(held)}/{decided} held" if decided else "undecided"
    print(f"  {key:18} n={len(rs):3}  {rate}")

bad = [r for r in scored if r["result"].lower() == "correct"
       and (r.get("strike_held") or "").lower() == "false"]
if bad:
    print(f"\n{len(bad)} call(s) were RIGHT on direction but the strike was blown "
          f"(a winning read on a losing spread):")
    for r in bad[:8]:
        print(f"  {r.get('date')} {r.get('msg_ref')} — {r.get('prediction', '')[:70]}")
