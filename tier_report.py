"""tier_report.py — promotion-ladder scoreboard.

Per risk tier: completed count, wins, net, expectancy, max drawdown, and how many
are open right now. A tier is only promotable when its sample is large enough and
expectancy is positive — this prints the raw numbers, the judgement stays human.

Run: python tier_report.py [days]     (default 30 days)
"""
import collections
import datetime
import json
import sys

import james_pipe as jp

TIERS = ("bankable", "middle", "high")
BADGE = {"bankable": "BANKABLE", "middle": "MIDDLE", "high": "HIGH"}
MIN_SAMPLE = 30
FLOOR_PER_TRADE = 40.0

days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
cut = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
st = json.loads(jp.STATE.read_text(encoding="utf-8"))

closed = [t for t in st.get("closed", {}).values()
          if t.get("pnl") is not None and not str(t.get("id", "")).startswith("TEST")
          and (t.get("exit_ts") or "")[:10] >= cut]
open_ = list(st.get("open", {}).values())

print(f"risk-tier report — last {days} days (cut {cut})\n")
for tier in TIERS:
    rows = [t for t in closed if t.get("tier") == tier]
    opn = [t for t in open_ if t.get("tier") == tier]
    n = len(rows)
    if n:
        pnls = [t["pnl"] for t in rows]
        net = sum(pnls)
        w = sum(1 for p in pnls if p > 0)
        exp = net / n
        run, dd = 0.0, 0.0
        for p in sorted(rows, key=lambda r: r.get("exit_ts") or ""):
            run += p["pnl"]
            dd = min(dd, run)
        verdict = []
        if n < MIN_SAMPLE:
            verdict.append(f"sample {n}/{MIN_SAMPLE}")
        if exp < FLOOR_PER_TRADE:
            verdict.append(f"below ${FLOOR_PER_TRADE:.0f}/trade floor")
        if net <= 0:
            verdict.append("net <= 0")
        state = "PROMOTABLE" if not verdict else "hold: " + ", ".join(verdict)
        print(f"{BADGE[tier]:9} done {n:3} | {w}W/{n - w}L | net ${net:+9.2f} | "
              f"exp ${exp:+.2f}/trade | maxDD ${dd:+.2f} | open {len(opn)}")
        print(f"{'':9} -> {state}")
    else:
        print(f"{BADGE[tier]:9} no completed trades | open {len(opn)}")


untiered = [t for t in closed if not t.get("tier")]
if untiered:
    print(f"\n{len(untiered)} closed trade(s) predate the tier field "
          f"({sum(t['pnl'] for t in untiered):+.2f} net) — excluded from tiers")
