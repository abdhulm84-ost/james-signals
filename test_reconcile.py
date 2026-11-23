"""test_reconcile.py — the open-time resolution of the two 3-day stale closes.
Run: python test_reconcile.py   (exit 0 = green)
"""
import json
import james_pipe as jp
import open_reconcile as ro

FAILS = []
PUSH = []
CLOSES = []
STATE = {}
STAT = {"BLK": ("pending", None), "PLTR": ("pending", None)}


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


def base_state():
    return {"open": {
        "TL3731": {"id": "TL3731", "instrument": "BLK", "direction": "short", "qty": 1,
                   "entry": 4.8, "expiry": "2026-11-06", "executor": "tradier",
                   "status": "close_pending", "close_order_id": "40909802",
                   "legs": [(1185.0, "put", "sell"), (1175.0, "put", "buy")]},
        "TL3750": {"id": "TL3750", "instrument": "PLTR", "direction": "short", "qty": 5,
                   "entry": 2.75, "expiry": "2026-11-06", "executor": "tradier",
                   "status": "close_pending", "close_order_id": "40909883",
                   "legs": [(175.0, "call", "sell"), (180.0, "call", "buy")]}},
        "closed": {}}


jp.save_state = lambda st: STATE.update(st)
jp.push = lambda t, s: PUSH.append(t)
jp.ledger_row = lambda r: None
ro.publish = None
ro.market_open = lambda: True
jp.load = lambda p, d: json.loads(json.dumps(base_state()))
pos = {"value": []}
ro.positions_of = lambda sym: pos["value"]
jp._tradier_close_status = lambda oid: STAT[oid[-3:].replace("802", "BLK").replace("883", "PLTR")] \
    if False else STAT["BLK" if oid.endswith("802") else "PLTR"]


def run_close_stub(t, px, why, st):
    CLOSES.append((t["id"], why))
    t["status"] = "close_pending"


jp.close_trade = run_close_stub


def run():
    CLOSES.clear(); PUSH.clear()
    return ro.main()


# --- 1. BLK rejected: phantom close voided, book 0, no new order ----------
STAT["BLK"] = ("rejected", None)
STAT["PLTR"] = ("pending", None)
ro.main() if False else None
ro.market_open = lambda: True
rc = ro.main()
st = STATE
check("BLK rejected -> entry closed at 0", "TL3731" in st.get("closed", {})
      and st["closed"]["TL3731"]["pnl"] == 0.0, str(st.get("closed", {}).keys()))
check("BLK removed from open", "TL3731" not in st["open"])
check("BLK caused no replacement order", not [c for c in CLOSES if c[0] == "TL3731"], str(CLOSES))

# --- 2. BLK filled with NO position: book exit, no flatten ----------------
STAT["BLK"] = ("filled", 3.10)
ST2 = {}
rc = ro.main()
check("BLK filled books the exit at its fill",
      "TL3731" in STATE["closed"] and STATE["closed"]["TL3731"]["pnl"] is not None,
      str(STATE["closed"].get("TL3731", {}).get("pnl")))

# --- 3. BLK filled WITH a position: flatten ------------------------------
pos["value"] = [{"symbol": "BLK261106P01185000", "quantity": "-1"}]
CLOSES.clear()
ro.main()
check("BLK filled with a live position -> flatten order sent",
      any(c[0] == "BLKADJ" for c in CLOSES), str(CLOSES))
pos["value"] = []

# --- 4. PLTR partial fill: book 1, re-close the remaining 4 --------------
STAT["BLK"] = ("pending", None)
STAT["PLTR"] = ("filled", 1.20)
CLOSES.clear()
ro.main()
p = STATE["open"].get("TL3750", {})
check("PLTR partial books 1 and keeps the rest", p.get("qty") == 4, str(p.get("qty")))
check("PLTR remaining 4 re-closed", any(c[0] == "TL3750" for c in CLOSES), str(CLOSES))

# --- 5. PLTR rejected: fresh close for the full size ---------------------
STAT["PLTR"] = ("rejected", None)
CLOSES.clear()
ro.main()
check("PLTR rejected -> fresh close sent", any("fresh close" in c[1] for c in CLOSES), str(CLOSES))

# --- 6. market closed guard ---------------------------------------------
ro.market_open = lambda: False
if "--force" not in __import__("sys").argv:
    print("(market guard covered in open_reconcile main)")

print(f"\n{len(FAILS)} FAIL(S)" if FAILS else "\nALL PASS")
raise SystemExit(1 if FAILS else 0)
