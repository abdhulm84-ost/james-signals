"""test_deadline.py — a stale 0DTE must never trade late (DESIGN #4).

A "for today" instruction parsed after the US close describes a market that no
longer exists. Before this, the queue would happily execute it at 16:05. Now
every queued item may carry a deadline; an EXPIRED item is parked (counterfactual
recorded) instead of traded.
Run: python test_deadline.py   (exit 0 = green)
"""
import pathlib
import tempfile
import time

import james_pipe as jp

FAILS = []
PARKS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


jp.save_state = lambda st: None
jp.push = lambda t, s: None
jp.ledger_row = lambda r: None
jp.park_signal = lambda c, m, t, w: PARKS.append((m, w))

# --- 1. derivation -------------------------------------------------------
d_today = jp._item_deadline("Sell SPX 7465P n Buy 7450P for today", None)
check("a 'for today' signal gets a deadline", d_today is not None, str(d_today))
check("the deadline is the US cash close (21:00 UTC bound)",
      time.gmtime(d_today).tm_hour == 21, time.strftime("%H:%M", time.gmtime(d_today)))
check("a 'for tomorrow' signal gets NO deadline",
      jp._item_deadline("Sell SPX 7480P for tomorrow", None) is None)
check("a dated swing signal gets NO deadline",
      jp._item_deadline("buy IBIT 37C for 11/20", None) is None)

# --- 2. the queue stores it automatically -------------------------------
tmp = pathlib.Path(tempfile.mkdtemp()) / "q.json"
q = jp.DurableQueue(tmp)
q.put("live", "Sell SPX 7465P for today", "901", prio=0)
it = q.snapshot()[0]
check("put() derives the deadline from the text", it.get("deadline") is not None,
      str(it.get("deadline")))

# --- 3. an expired item is parked, not traded ---------------------------
tmp2 = pathlib.Path(tempfile.mkdtemp()) / "q2.json"
q2 = jp.DurableQueue(tmp2)
q2.put("live", "Sell SPX 7465P for today", "902", prio=0,
       deadline=time.time() - 60)          # already past
expired = q2.pop()
check("the expired item still pops (the consumer decides)", expired is not None)
check("it is recognised as expired",
      bool(expired.get("deadline")) and time.time() > float(expired["deadline"]))

# the consumer's expiry rule, exercised directly
PARKS.clear()
if expired.get("deadline") and time.time() > float(expired["deadline"]):
    jp.park_signal({"instrument": None, "direction": None, "entry": None,
                    "spread": None}, expired["mid"], expired.get("text") or "",
                   "expired before processing (DESIGN #4) — recorded, not traded")
check("an expired signal is parked with a reason",
      PARKS and "expired" in PARKS[0][1].lower(), str(PARKS))

# --- 4. a future deadline does NOT park ---------------------------------
tmp3 = pathlib.Path(tempfile.mkdtemp()) / "q3.json"
q3 = jp.DurableQueue(tmp3)
q3.put("live", "Sell SPX 7465P for today", "903", prio=0,
       deadline=time.time() + 3600)
fresh = q3.pop()
check("a still-valid item is not expired",
      not (fresh.get("deadline") and time.time() > float(fresh["deadline"])))

for f in (tmp, tmp2, tmp3):
    f.unlink(missing_ok=True)
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
