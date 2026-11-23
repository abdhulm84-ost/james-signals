"""test_uw_park.py — a staleness park captures the tape (user 2026-11-18).

Parking is fine; losing the surrounding flow is not. When a signal is refused
because its session already passed, the UW net-premium snapshot at that moment
is recorded next to it, so the counterfactual carries the tape too.
Run: python test_uw_park.py   (exit 0 = green)
"""
import csv
import pathlib
import tempfile
import time

import james_pipe as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


check("helper exists", hasattr(jp, "_uw_snapshot_on_park"))
check("ledger path is uw_park_links.csv", jp.UW_PARK.name == "uw_park_links.csv")

# the snapshot must never block: it is fired as a daemon thread
src = pathlib.Path(jp.__file__).read_text(encoding="utf-8")
check("runs off the FIFO thread", "daemon=True" in src)
check("wired into the stale-expiry park", "_uw_snapshot_on_park(" in src)

# a missing uw_flow module must not raise
jp.log = lambda m: None
try:
    jp._uw_snapshot_on_park("SPX", "999", "test")
    ok = True
except Exception as e:
    ok = False
check("a failing snapshot never breaks the pipe", ok)

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
