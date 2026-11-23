"""test_ledger_ts.py — a ledger row must carry a timestamp (D41).

3,078 of 3,087 signals.csv rows went out with a blank ts: callers omit the
column and ledger_row's row.get fell through to "", so time-window queries
silently returned nothing (a 'no trades since X' conclusion built on a broken
clock). Stamp when missing; a caller-supplied ts still wins.
Run: python test_ledger_ts.py   (exit 0 = green)
"""
import csv
import os

import james_pipe as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


tmp = jp.HERE / "signals_test.csv"
jp.LEDGER = tmp
if tmp.exists():
    tmp.unlink()

jp.ledger_row({"event": "ORDER", "trade_id": "T1", "reason": "no ts given"})
with tmp.open(encoding="utf-8") as f:
    rows = list(csv.DictReader(f))
check("a row without ts is stamped", rows and rows[0]["ts"].strip() != "", str(rows[:1]))
check("stamp has second precision",
      rows and len(rows[0]["ts"]) == 19 and rows[0]["ts"][4] == "-", rows[0]["ts"])

jp.ledger_row({"ts": "2026-01-02 03:04:05", "event": "EXIT", "reason": "caller ts"})
with tmp.open(encoding="utf-8") as f:
    rows = list(csv.DictReader(f))
check("caller-supplied ts still wins", rows[1]["ts"] == "2026-01-02 03:04:05", rows[1]["ts"])

tmp.unlink()
jp.LEDGER = jp.HERE / "signals.csv"
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
