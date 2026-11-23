"""test_park_dedupe.py — one park row per event, not per reprocessing (D42).

A test spy forwarded to the real park_signal and re-parked fixture messages on
every suite run (msg 928 reached 312 copies in the production register). The
register must thicken only on NEW information: same msg+reason+raw = one row,
a different reason on the same message = a new row (it is information).
Run: python test_park_dedupe.py   (exit 0 = green)
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
jp._PK_SEEN = None                      # fresh index against the temp register
if tmp.exists():
    tmp.unlink()

card = {"instrument": "SPX", "direction": "short", "entry": 4.0,
        "spread": {"type": "credit", "legs": [
            {"action": "sell", "strike": 7600.0, "right": "put"},
            {"action": "buy", "strike": 7590.0, "right": "put"}]},
        "confidence": 70, "horizon": "day", "rationale": "x"}
text = "Sell 7600P n Buy 7590P n collect $475 for 11/20"

for _ in range(3):
    jp.park_signal(card, "928", text, "credit guard: test reason")
rows = list(csv.DictReader(tmp.open(encoding="utf-8")))
check("3 identical re-parks -> 1 row", len(rows) == 1, str(len(rows)))

jp.park_signal(card, "928", text, "a DIFFERENT reason for the same msg")
rows = list(csv.DictReader(tmp.open(encoding="utf-8")))
check("different reason -> kept as new row", len(rows) == 2, str(len(rows)))

jp.park_signal(card, "929", text, "credit guard: test reason")
rows = list(csv.DictReader(tmp.open(encoding="utf-8")))
check("different msg -> new row", len(rows) == 3, str(len(rows)))

# the index seeds from the register already on disk (process restart / new run)
jp._PK_SEEN = None
jp.park_signal(card, "928", text, "credit guard: test reason")
rows = list(csv.DictReader(tmp.open(encoding="utf-8")))
check("index reseeds from disk (no duplicate after restart)",
      len(rows) == 3, str(len(rows)))

tmp.unlink()
jp.PKL = jp.HERE / "parking_lot.csv"
jp._PK_SEEN = None
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
