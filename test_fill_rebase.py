"""test_fill_rebase.py — stops/targets must follow the REAL fill (KTOS).

The card said 1.28 (it belonged to the neighbouring DBX message) while the order
filled at 3.90 on a market single, leaving the stop 68% away. Levels now rebase
proportionally, and a fill far from the card is flagged.
Run: python test_fill_rebase.py   (exit 0 = green)
"""
import james_pipe as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


jp.log = lambda m: None

t = {"id": "T1", "entry": 1.28, "stop": 1.26, "targets": [1.5, 2.0], "qty": 7}
jp.rebase_levels(t, 3.9)
check("stop scales to the fill", abs(t["stop"] - 3.84) < 0.02, str(t["stop"]))
check("targets scale too", abs(t["targets"][0] - 4.57) < 0.05, str(t["targets"]))
check("entry is the real fill", t["entry"] == 3.9)
check("far fill is flagged", t.get("card_entry_far") is True)
check("stop now sits below the fill", t["stop"] < t["entry"])

t2 = {"id": "T2", "entry": 2.75, "stop": 2.71, "targets": []}
jp.rebase_levels(t2, 2.70)
check("a stop above the fill is pulled below it", t2["stop"] < t2["entry"], str(t2))

t3 = {"id": "T3", "entry": 0.9, "stop": 0.88, "targets": []}
jp.rebase_levels(t3, 0.9)
check("an equal fill changes nothing", t3["stop"] == 0.88)
check("no false far flag", not t3.get("card_entry_far"))

print(f"\n{len(FAILS)} FAIL(S)" if FAILS else "\nALL PASS")
raise SystemExit(1 if FAILS else 0)
