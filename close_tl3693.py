"""One-off manual broker close for TL3693 (pre-fix mis-parse: 'for today' became an
11/06 expiry, the 'coleect' typo left no limit price, and 'double size' was ignored).

Places the reverse multileg (buy 7750C / sell 7760C) at Tradier sandbox. Booking is
left to the running pipe via close.txt so its in-memory state stays authoritative.
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

import james_pipe as P
from james_core import Record, Leg, MsgClass, Action

EXP = "2026-11-06"
legs = [
    Leg(strike=7750.0, right="call", action="buy", qty=1, underlying="SPXW", expiry=EXP),
    Leg(strike=7760.0, right="call", action="sell", qty=1, underlying="SPXW", expiry=EXP),
]
print("closing OCC legs:", [l.occ() for l in legs])
rec = Record(msg_id=3693, ts=P.now(), cls=MsgClass.VERTICAL, action=Action.EXIT,
             raw="manual close of mis-parsed TL3693", underlying="SPXW",
             legs=legs, credit=0.0, qty=1, expiry=EXP, direction="short")
ex = P._TRADIER().place(rec)
print(f"ok={ex.ok} order={ex.order_id} venue={ex.venue} reason={ex.reason}")
sys.exit(0 if ex.ok else 1)
