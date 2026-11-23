"""test_earnings_guard.py — the earnings calendar as an advisory risk gate.

James trades earnings constantly and they have hurt us: WPM was a "Bullish for
WPM Earnings" bull put with a 15-day expiry (an exit AFTER the binary event)
and was stopped for -$960; CHTR -$4,900. The pipeline now knows an underlying
is reporting WITHOUT relying on James saying so.

Advisory only: it warns and records, never blocks, never sizes.
Run: python test_earnings_guard.py   (exit 0 = green)
"""
import csv
import datetime
import pathlib
import tempfile

import earnings_guard as eg
import james_pipe as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


# --- parser: the finviz row shape ---------------------------------------
sample = ("| ![Image 1: X logo](https://logo.finviz.com/X.svg)X[ABCD]"
          "(https://finviz.com/stock?t=ABCD) | Some Corp | AMC | $2.90B |")
m = eg.ROW.search(sample)
check("parses ticker/company/when/mcap from a finviz row",
      m and m.group(1) == "ABCD" and m.group(3) == "AMC", str(m.groups() if m else None))

# --- calendar + advisory ------------------------------------------------
tmp = pathlib.Path(tempfile.mkdtemp()) / "cal.csv"
orig = eg.CAL
eg.CAL = tmp
today = datetime.date.today()
soon = (today + datetime.timedelta(days=2)).isoformat()
far = (today + datetime.timedelta(days=9)).isoformat()
with tmp.open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=eg.COLS)
    w.writeheader()
    w.writerow({"date": soon, "ticker": "WPM", "company": "Wheaton",
                "when": "AMC", "market_cap": "$5B", "fetched": "x"})

line = eg.check("WPM", far)
check("a trade that OUTLIVES the report is flagged",
      "HOLDS THROUGH" in line, line)
line2 = eg.check("WPM", (today + datetime.timedelta(days=1)).isoformat())
check("a trade that exits BEFORE the report is not flagged as holding",
      "HOLDS THROUGH" not in line2 and "before the event" in line2, line2)
check("an unknown ticker yields no advisory", eg.check("NOPE", far) == "")

# --- never blocks a trade ----------------------------------------------
jp.log = lambda m: None
jp.ledger_row = lambda r: None
try:
    jp._earnings_advisory("WPM", far, "999")
    ok = True
except Exception as e:
    ok = False
check("the advisory never raises (never blocks a trade)", ok)

eg.CAL = orig
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
