"""test_d131_orphan.py — D131 guards (2026-11-20).

D131: book-vs-broker qty divergence. Three bites of one bug family:
  1. D72's fix capped BOOK rows to 1 lot but the broker orders were already
     resting at 10 — the fills recorded 1 while the broker held 10, and the
     exits closed 1 → 9-lot orphans (TL4411/TL4403) riding unmanaged.
  2. The boot-time D72 cap then cut the ADOPTED repair row (qty 9 → 1), so its
     stop-close covered 1 of 9 (order 41931908, cancelled in repair).
  3. adopt() would happily adopt an EXPIRED OCC lingering past settlement.

Guards tested here:
  - d72_boot_cap caps ONLY rows we still control (pending, no broker order);
    filled/adopted/resting rows keep broker truth.
  - adopt() skips expired OCCs and writes an OPEN lifecycle for what it does
    adopt.

Run: python test_d131_orphan.py   (exit 0 = green)
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import james_pipe as jp
import reconcile_positions as rp

FAILS = []
LOGS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


jp.log = LOGS.append
jp.ledger_row = lambda r: None

# ---- d72_boot_cap: only unsent rows may be capped ------------------------
st = {"open": {
    "A-SENTLESS": {"id": "A-SENTLESS", "qty": 10, "status": "pending_entry"},
    "B-FILLED": {"id": "B-FILLED", "qty": 9, "status": "open_tradier"},
    "C-RESTING": {"id": "C-RESTING", "qty": 10, "status": "pending_entry",
                  "order_id": 555},
    "D-ADOPTED": {"id": "D-ADOPTED", "qty": 9, "status": "close_pending"},
    "E-ATCAP": {"id": "E-ATCAP", "qty": 1, "status": "pending"},
}}
over = jp.d72_boot_cap(st)
check("unsent pending row is capped",
      st["open"]["A-SENTLESS"]["qty"] == 1, str(st["open"]["A-SENTLESS"]["qty"]))
check("filled row keeps broker qty",
      st["open"]["B-FILLED"]["qty"] == 9, str(st["open"]["B-FILLED"]["qty"]))
check("resting order row keeps order qty (book mirrors the order)",
      st["open"]["C-RESTING"]["qty"] == 10, str(st["open"]["C-RESTING"]["qty"]))
check("adopted/close_pending row untouched",
      st["open"]["D-ADOPTED"]["qty"] == 9, str(st["open"]["D-ADOPTED"]["qty"]))
check("row already at cap untouched",
      st["open"]["E-ATCAP"]["qty"] == 1 and "D72" not in (st["open"]["E-ATCAP"].get("fill_note") or ""),
      str(st["open"]["E-ATCAP"]))
check("capped list names exactly the unsent row",
      [t for t, _q in over] == ["A-SENTLESS"], str(over))
check("capped row carries the D72 note",
      "D72" in (st["open"]["A-SENTLESS"].get("fill_note") or ""), str(st["open"]["A-SENTLESS"]))

# ---- adopt(): dead OCCs are never adopted --------------------------------
dead = [("SPXW261101C07600000", -1, -500.0), ("SPXW261101C07590000", 1, 400.0)]
made = rp.adopt(dead, {"open": {}})
check("expired OCC pair is skipped", made == [], str(made))

made2 = rp.adopt(
    [("SPXW261219C07600000", -1, -500.0), ("SPXW261219C07590000", 1, 400.0)],
    {"open": {}})
check("live OCC pair is adopted", len(made2) == 1, str(made2))
if made2:
    tid, t = made2[0]
    check("adopted qty/entry reflect the broker",
          t["qty"] == 1 and t["entry"] == 1.0 and t["direction"] == "short",
          f"qty={t['qty']} entry={t['entry']} dir={t['direction']}")
    rows = __import__("trade_lifecycle").get_lifecycle(tid)
    check("adopted trade gets an OPEN lifecycle row",
          any(r.get("event") == "OPEN" for r in rows), str(rows))

# ---- fill-qty sync: book mirrors the broker's filled size (D131) -------
class _Q:
    def __init__(self, m):
        self.m = m

    def order_quantity(self, oid):
        return self.m.get(oid)


jp._TRADIER_INST = _Q({777: 20, 778: 1, 779: None})
t2 = {"id": "SYNC-A", "order_id": 777, "qty": 1, "instrument": "SPXW",
      "legs": [[7600.0, "put", "sell"], [7590.0, "put", "buy"]]}
jp.rebase_levels(t2, 1.23)
check("multileg fill syncs qty to spreads (raw 20 / 2 legs -> 10)",
      t2["qty"] == 10, str(t2["qty"]))
check("sync leaves an honest note",
      "qty synced to broker fill" in (t2.get("fill_note") or "")
      and "D131" in (t2.get("fill_note") or ""), str(t2.get("fill_note")))
t3 = {"id": "SYNC-B", "order_id": 778, "qty": 1,
      "legs": [[7600.0, "put", "sell"]]}
jp.rebase_levels(t3, 2.0)
check("single-option fill qty unchanged when it matches",
      t3["qty"] == 1, str(t3["qty"]))
t4 = {"id": "SYNC-C", "order_id": 779, "qty": 3,
      "legs": [[7600.0, "put", "sell"], [7590.0, "put", "buy"]]}
jp.rebase_levels(t4, 5.0)
check("missing broker qty -> book untouched",
      t4["qty"] == 3, str(t4["qty"]))
jp._TRADIER_INST = None

# ---- D132: suite must never reach live channels ------------------------
ex = jp._TRADIER()
check("test context gets a refusing executor",
      ex.place(None).ok is False and ex.close(None).ok is False
      and ex.order_fill(1) == (None, None) and ex.cancel(1)[0] is False,
      "stub failed")
jp._TRADIER_INST = None
import inspect
check("test context detected under test argv", jp._is_test_run() is True)
check("ledger_row routes suite writes out of live signals.csv",
      "signals.test.csv" in inspect.getsource(jp))
check("_push_send refuses the live session under test",
      "test context" in inspect.getsource(jp._push_send)
      or "_is_test_run" in inspect.getsource(jp._push_send))

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
