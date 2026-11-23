"""test_park_full_raw.py — the park register stores the FULL message (D46).

A park row once kept only raw[:200]; narrative messages carry their legs in
the TAIL, so a settle re-parsed a truncated story and booked the wrong
outcome. D36's rule — keep the full text for parsing, truncate only at
display — now covers the park register too.
Run: python test_park_full_raw.py   (exit 0 = green)
"""
import csv

import james_pipe as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


tmp = jp.HERE / "parking_lot_test.csv"
jp.PKL = tmp
jp._PK_SEEN = None
if tmp.exists():
    tmp.unlink()

LONG = ("Until 1pm CST, Market will be choppy. So we can do an Iron fly in mean "
        "time n close it before FOMC. " + "pad " * 30 +          # > 200 chars before the legs
        "Sell 7735C/P, Buy 7730P n Buy 7740C n collect $475 for 11/12, $25 risk and $475 reward")
check("fixture really pushes the legs past 200 chars", LONG.index("Sell 7735") > 200)

jp.park_signal({"instrument": "SPX", "direction": "short", "entry": None, "qty": 1,
                "spread": {"type": "credit", "legs": [
                    {"action": "sell", "strike": 7735.0, "right": "call"},
                    {"action": "buy", "strike": 7740.0, "right": "call"}]},
                "confidence": 60, "horizon": "day", "rationale": "x"},
               "3469t", LONG, "structure-labelled test")
row = list(csv.DictReader(tmp.open(encoding="utf-8")))[0]
check("park row keeps the full text (legs in the tail survive)",
      len(row["raw"]) > 200 and "Sell 7735C/P" in row["raw"], f"len={len(row['raw'])}")

# the settle must now see the fly legs and credit from the tail
jp._PX_CACHE.clear()
jp._expiry_close = lambda s, d: 7740.0
import datetime as _dt
jp.us_today = lambda: _dt.date(2026, 11, 13)   # pin the clock: 'for 11/12' is settled, not pending
o = jp.park_outcome(row)
check("settle parses the tail legs (4-leg fly, credit 4.75)",
      o is not None and o["credit"] == 4.75 and o["structure"] == "short_iron_butterfly",
      str({k: o.get(k) for k in ("credit", "structure", "would_pnl")}))
check("outcome row carries the full raw too",
      o and len(o["raw"]) > 200 and "Sell 7735C/P" in o["raw"])

tmp.unlink()
jp.PKL = jp.HERE / "parking_lot.csv"
jp._PK_SEEN = None
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
