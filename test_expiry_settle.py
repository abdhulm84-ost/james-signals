"""test_expiry_settle.py — expired options stop retrying a dead close order.

BLK/PLTR/AD sat pending for days after expiry. Once the broker is flat the trade
settles: phantom (entry never filled) at zero, or intrinsic from the official
expiry close.
Run: python test_expiry_settle.py   (exit 0 = green)
"""
import james_pipe as jp

FAILS = []
PUSH = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


jp.save_state = lambda st: None
jp.ledger_row = lambda r: None
jp.push = lambda t, s: PUSH.append(t)
jp._positions_now = lambda: {}
jp._expiry_close = lambda sym, day: 100.0
jp.us_today = lambda: __import__("datetime").date(2026, 11, 7)
STATUS = {"o1": "expired", "o2": "filled"}


class _T:
    def cancel(self, oid):
        return True, "cancelled"


jp._TRADIER_INST = _T()
jp._tradier_close_status = lambda oid: (STATUS.get(oid), 0.0)


def trade(tid, order, legs, entry=2.0, qty=2):
    return {"id": tid, "instrument": "SPXW", "symbol": "SPXW", "legs": legs,
            "direction": "short", "entry": entry, "qty": qty,
            "expiry": "2026-11-05", "status": "close_pending", "executor": "tradier",
            "order_id": order, "opt_underlying": "SPX", "opt_id": "", "targets": [],
            "stop": None}


# entry order expired -> phantom, 0, not an intrinsic guess
st = {"open": {"A": trade("A", "o1", [[7600.0, "put", "sell"], [7590.0, "put", "buy"]])},
      "closed": {}}
jp.settle_expired(st)
t = st["closed"].get("A", {})
check("unfilled entry is voided at 0", t.get("pnl") == 0.0, str(t.get("pnl")))
check("phantom note names the reason", "never held" in str(t.get("fill_note")), str(t.get("fill_note")))

# entry filled -> settle intrinsic at the expiry close
st = {"open": {"B": trade("B", "o2", [[7600.0, "put", "sell"], [7590.0, "put", "buy"]])},
      "closed": {}}
jp.settle_expired(st)
t = st["closed"].get("B", {})
check("filled spread settles at intrinsic", t.get("pnl") == -1600.0, str(t.get("pnl")))
check("settlement names the close", "100" in str(t.get("fill_note")), str(t.get("fill_note")))

# not expired yet -> untouched
st = {"open": {"C": dict(trade("C", "o2", []), expiry="2026-12-01")}, "closed": {}}
jp.settle_expired(st)
check("future expiry untouched", "C" in st["open"])

# still at the broker -> untouched (keep closing)
jp._positions_now = lambda: {"SPXW261105P07600000": -2}
st = {"open": {"D": trade("D", "o2", [[7600.0, "put", "sell"], [7590.0, "put", "buy"]])},
      "closed": {}}
jp.settle_expired(st)
check("position still at broker untouched", "D" in st["open"])

print(f"\n{len(FAILS)} FAIL(S)" if FAILS else "\nALL PASS")
raise SystemExit(1 if FAILS else 0)
