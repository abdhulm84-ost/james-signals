"""test_phantom_recover.py — D111 phantom-close deal-history recovery (2026-11-19).

The original miss: two NDX shorts (B1795007219, B1794925335) hit their targets
at the broker, but by the time the pipe tried to close them the MT5 position
was already gone ('position not found'). D99 booked them closed-at-zero — a
winner collected as $0. Live recovery proves both actually closed: +10.38 and
+12.64.

Rule: 'position not found' is TERMINAL for retry, but NOT proof of a phantom —
query the deal history for the position's exit deal and book the REAL close.
Only when no exit deal exists is it a true phantom (zero).
Run: python test_phantom_recover.py   (exit 0 = green)
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import bb_executor as bb

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


# the two phantom tickets must recover a real exit deal (live MT5 history)
r1 = bb.recover_closed_deal(238286745)
r2 = bb.recover_closed_deal(237762508)
check("B1795007219 recovers a real close price",
      r1 is not None and r1.get("price") is not None and float(r1["price"]) > 0,
      str(r1))
check("B1794925335 recovers a real close price",
      r2 is not None and r2.get("price") is not None and float(r2["price"]) > 0,
      str(r2))

# a ticket that never existed returns None (true phantom)
check("unknown ticket returns None (true phantom)",
      bb.recover_closed_deal(0) is None)

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
