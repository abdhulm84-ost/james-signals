#!/usr/bin/env python3
"""park_report.py — what the PARKED trades would have done.

Parking is fine; losing the outcome is not. settle_parked() books every parked
structure's would-have P&L at its expiry close into park_outcomes.csv; this
reads the register back and answers the question the register exists for:
which direction are these trades going, and what is worth taking next time?

  python park_report.py            # the breakdown
  python park_report.py --settle   # settle new rows first, then report
  python park_report.py --rows 30  # show the last N settled rows

Read-only against the brokers. Per-lot dollars (a park never carries qty).
"""
import argparse
import csv
import sys
from collections import defaultdict

import james_pipe as jp


def verdict_class(v):
    if v.startswith("WOULD HAVE WON"):
        return "won"
    if v.startswith("would have lost"):
        return "lost"
    return "other"


def bucket(rows, keyfn, label):
    b = defaultdict(lambda: [0, 0, 0, 0.0])   # n, won, lost, net
    for r in rows:
        k = keyfn(r) or "(none)"
        e = b[k]
        e[0] += 1
        c = verdict_class(r["verdict"])
        if c == "won":
            e[1] += 1
        elif c == "lost":
            e[2] += 1
        try:
            e[3] += float(r["would_pnl"])
        except (TypeError, ValueError):
            pass
    print(f"\n== by {label} ==")
    for k, (n, w, l, net) in sorted(b.items(), key=lambda kv: -kv[1][3]):
        dec = w + l
        wr = f"{100.0 * w / dec:.0f}% won" if dec else "n/a"
        print(f"  {k:<28} {n:>4} rows  {wr:>10}  net ${net:>+10,.2f}")
    return b


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--settle", action="store_true",
                    help="run settle_parked first (writes new outcome rows)")
    ap.add_argument("--rows", type=int, default=0,
                    help="show the last N settled rows")
    a = ap.parse_args()

    if a.settle:
        n = jp.settle_parked(None)
        print(f"settled {n} new parked row(s)")

    if not jp.PARK_OUT.exists():
        print("no park_outcomes.csv yet — run: python park_report.py --settle")
        return 0
    with jp.PARK_OUT.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("park_outcomes.csv is empty")
        return 0

    dec = [r for r in rows if verdict_class(r["verdict"]) in ("won", "lost")]
    won = [r for r in dec if verdict_class(r["verdict"]) == "won"]
    lost = [r for r in dec if verdict_class(r["verdict"]) == "lost"]
    other = [r for r in rows if verdict_class(r["verdict"]) == "other"]
    net = 0.0
    for r in rows:
        try:
            net += float(r["would_pnl"])
        except (TypeError, ValueError):
            pass

    print(f"=== PARKED OUTCOMES ({len(rows)} rows) ===")
    print(f"decided {len(dec)}: {len(won)} would have won / {len(lost)} would have lost"
          + (f"  ({len(other)} unsettled/other)" if other else ""))
    if dec:
        print(f"hit rate {100.0 * len(won) / len(dec):.1f}%   "
              f"per-lot net would-have ${net:+,.2f}")
    gross_w = sum(float(r["would_pnl"]) for r in won if r["would_pnl"] != "")
    gross_l = sum(float(r["would_pnl"]) for r in lost if r["would_pnl"] != "")
    if won and lost:
        print(f"avg win ${gross_w / len(won):+,.2f}  "
              f"avg loss ${gross_l / len(lost):+,.2f}  "
              f"payoff {abs(gross_w / gross_l):.2f}:1")

    bucket(rows, lambda r: r["direction"], "direction (the question)")
    bucket(rows, lambda r: r["structure"], "structure")
    bucket(rows, lambda r: r["underlying"], "underlying")

    def quality(r):
        try:
            c, w = float(r["credit"]), float(r["width"])
        except (TypeError, ValueError):
            return "(no credit/width)"
        if not w:
            return "(no width)"
        q = c / w
        return "credit/width >= 0.55" if q >= 0.55 else "credit/width < 0.55"
    bucket(rows, quality, "credit quality")

    if a.rows:
        print(f"\n== last {a.rows} settled rows ==")
        for r in rows[-a.rows:]:
            print(f"  {r['ts'][:16]} msg {r['msg_id']:<8} {r['underlying'] or '?':<7}"
                  f" {r['direction']:<8} cr {r['credit'] or '-':>6}"
                  f" px {r['settle_px'] or '-':>9}"
                  f" pnl {r['would_pnl'] or '-':>9}  {r['verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
