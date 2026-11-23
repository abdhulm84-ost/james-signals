"""test_supersede.py — a retraction retires a queued-but-unrun message (DESIGN #3).

"Scratch that" / "ignore that" arrives AFTER an entry is queued but BEFORE it
runs. Without this, the stale intent still executes. Rules are deliberately
narrow: only EXPLICIT withdrawal retires a signal — a message that merely
contradicts an earlier one is ordinary trading, not a retraction.
Run: python test_supersede.py   (exit 0 = green)
"""
import pathlib
import tempfile

import james_pipe as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


def fresh_q():
    return jp.DurableQueue(pathlib.Path(tempfile.mkdtemp()) / "q.json")


# --- 1. "ignore that" retires the newest pending entry -------------------
q = fresh_q()
q.put("live", "Sell SPX 7520P n Buy 7505P", "500", prio=0)
targets = jp._supersede_targets("ignore that", "501", q)
check("'ignore that' retires the newest pending entry",
      targets == ["500"], str(targets))

# --- 2. a plain narrative retires nothing ------------------------------
q2 = fresh_q()
q2.put("live", "Sell SPX 7520P", "600", prio=0)
check("ordinary commentary retires nothing",
      jp._supersede_targets("SPX looks heavy here", "601", q2) == [])

# --- 3. contradiction is NOT retraction --------------------------------
check("a contradicting signal does not retire the earlier one",
      jp._supersede_targets("actually buy calls now", "602", q2) == [])

# --- 4. "cancel TICKER" retires matching pending entries ---------------
q3 = fresh_q()
q3.put("live", "Sell SPX 7520P", "700", prio=0)
q3.put("live", "Sell QQQ 664P", "701", prio=0)
t3 = jp._supersede_targets("cancel SPX", "702", q3)
check("'cancel SPX' retires only the SPX entry", t3 == ["700"], str(t3))

# --- 5. never retract FORWARD (a newer queued item survives) ----------
q4 = fresh_q()
q4.put("live", "Sell SPX 7520P", "800", prio=0)
check("a retraction from a NEWER id does not retire an older queued item",
      jp._supersede_targets("scratch that", "899", q4) == ["800"],
      "expects the older item IS the target (retraction is backward-looking)")
# and the guard itself: an id LOWER than the queued one must not retract
q5 = fresh_q()
q5.put("live", "Sell SPX 7520P", "900", prio=0)
check("an older message cannot retract a newer queued one",
      jp._supersede_targets("scratch that", "700", q5) == [])

# --- 6. drop() actually removes it -----------------------------------
q6 = fresh_q()
q6.put("live", "Sell SPX 7520P", "950", prio=0)
before = q6.depth()
for v in jp._supersede_targets("scratch that", "951", q6):
    q6.drop(v)
check("the retracted message is gone from the queue",
      q6.depth() == before - 1, f"{before} -> {q6.depth()}")

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
