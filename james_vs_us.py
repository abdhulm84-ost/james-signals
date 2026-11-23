#!/usr/bin/env python3
"""james_vs_us — compare James's call outcomes with OUR execution outcomes.

James side: james_outcomes.csv (captured when HIS book closes/trims: claimed %
first, else last P&L mark). Our side: state.json realized trades. Also reports
the mirrored subset (calls we actually took, linked by our_trade id).
"""
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def summarize(name, rows):
    w = sum(1 for r in rows if r["result"] == "win")
    l = sum(1 for r in rows if r["result"] == "loss")
    unk = sum(1 for r in rows if r["result"] == "unknown")
    print(f"{name:22} win {w:3}  loss {l:3}  unknown {unk:3}  "
          f"hit {100*w/max(1, w+l):.0f}%")


def main():
    jout = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "james_outcomes.csv"
    jrows = list(csv.DictReader(jout.open(encoding="utf-8"))) if jout.exists() else []
    print(f"James outcome rows: {len(jrows)}")
    summarize("JAMES (his calls)", jrows)
    mir = [r for r in jrows if (r.get("our_trade") or "").strip()]
    summarize("JAMES (ones we took)", mir)

    st = json.loads((HERE / "state.json").read_text(encoding="utf-8"))
    def _real(v):
        n = str(v.get("fill_note", "")).lower()
        return (v.get("pnl") not in (None, 0) and "never" not in n
                and "cleanup" not in n and "manual settle" not in n
                and not str(v.get("id", "")).startswith("TEST"))
    ours = [v for v in st.get("closed", {}).values() if _real(v)]
    wins = sum(1 for v in ours if v["pnl"] > 0)
    losses = sum(1 for v in ours if v["pnl"] < 0)
    aw = sum(v["pnl"] for v in ours if v["pnl"] > 0) / max(1, wins)
    al = abs(sum(v["pnl"] for v in ours if v["pnl"] < 0)) / max(1, losses)
    print(f"{'US (our trades)':22} win {wins:3}  loss {losses:3}            "
          f"hit {100*wins/max(1, wins+losses):.0f}%  payoff {aw/max(1e-9, al):.2f}:1  "
          f"net ${sum(v['pnl'] for v in ours):+.2f}")


if __name__ == "__main__":
    main()
