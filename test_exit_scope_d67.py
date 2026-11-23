"""test_exit_scope_d67.py — a trim must never cancel an UNFILLED entry (D67).

US Mon 2026-11-16: msg 4271 "Big drop on SPX, 7615 t1 to book 80% gains" is a
management command for a HELD position. The unnamed-exit scoper treats any
2-5 letter capitalised word as a ticker ("SPX"), matched every open SPX row —
including two UNFILLED pending entries — and cancelled them. One would have
won ~$3,560 (sell 7575C / buy 7590C, SPX closed 7615).

Rule pinned here: trim/exit acts on FILLED positions only. Pending entries are
left alone (they expire on their own), unless the text explicitly says
cancel/abandon.
Run: python test_exit_scope_d67.py   (exit 0 = green)
"""
import james_pipe as jp

FAILS = []
CLOSED = []
PUSHED = []
LEDGER = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


jp.save_state = lambda st: None
jp.push = lambda t, s: PUSHED.append(t)
jp.ledger_row = lambda r: LEDGER.append(r)
jp.park_signal = lambda c, m, t, w: None


def _close(t, px, why, st):
    CLOSED.append((t["id"], why))
    t["status"] = "close_pending"


jp.close_trade = _close
jp._TRADIER().cancel = lambda oid: (True, "ok")

PENDING = {"id": "TL4379", "instrument": "SPXW", "symbol": "SPXW",
           "direction": "short", "status": "pending_entry", "qty": 8,
           "entry": 4.45, "expiry": "2026-11-20", "order_id": "ORD4379",
           "legs": [[7575.0, "call", "sell"], [7590.0, "call", "buy"]],
           "stop": None, "targets": []}
FILLED = {"id": "TL4001", "instrument": "SPXW", "symbol": "SPXW",
          "direction": "short", "status": "open_tradier", "qty": 2,
          "entry": 5.0, "expiry": "2026-11-20", "order_id": "ORD1",
          "legs": [[7700.0, "call", "sell"], [7710.0, "call", "buy"]],
          "stop": 7.0, "targets": []}

# 1. THE INCIDENT: the trim must leave the pending entry untouched
CLOSED.clear()
PUSHED.clear()
st = {"open": {"TL4379": dict(PENDING)},
      "closed": {}, "alerts_today": 0, "seen_msg": {}}
jp.handle_exit_update("exit", {"instrument": "SPXW", "direction": "short"},
                      "4271", st, "Big drop on SPX, 7615 t1 to book 80% gains")
check("D67: a trim does NOT cancel an unfilled entry",
      not CLOSED and st["open"]["TL4379"]["status"] == "pending_entry",
      f"{CLOSED} {st['open']['TL4379']['status']}")

# 2. a filled position IS still trimmed
CLOSED.clear()
st2 = {"open": {"TL4001": dict(FILLED)}, "closed": {}, "alerts_today": 0,
       "seen_msg": {}}
jp.handle_exit_update("exit", {"instrument": "SPXW", "direction": "short"},
                      "4272", st2, "Big drop on SPX, 7615 t1 to book 80% gains")
check("D67: a FILLED position is still acted on", bool(CLOSED), str(CLOSED))

# 3. mixed book: filled closes, pending survives
CLOSED.clear()
st3 = {"open": {"TL4001": dict(FILLED), "TL4379": dict(PENDING)},
       "closed": {}, "alerts_today": 0, "seen_msg": {}}
jp.handle_exit_update("exit", {"instrument": "SPXW", "direction": "short"},
                      "4273", st3, "Big drop on SPX, 7615 t1 to book 80% gains")
check("D67: mixed book -> filled closed, pending survives",
      [c[0] for c in CLOSED] == ["TL4001"] and
      st3["open"].get("TL4379", {}).get("status") == "pending_entry",
      f"{CLOSED} pending={st3['open'].get('TL4379', {}).get('status')}")

# 4. an EXPLICIT cancel still cancels the pending entry
CLOSED.clear()
st4 = {"open": {"TL4379": dict(PENDING)}, "closed": {}, "alerts_today": 0,
       "seen_msg": {}}
jp.handle_exit_update("exit", {"instrument": "SPXW", "direction": "short"},
                      "4274", st4,
                      "Please cancel the unfilled SPX 7575/7590 entry")
check("D67: an explicit cancel instruction still cancels",
      bool(CLOSED) and CLOSED[0][0] == "TL4379", str(CLOSED))

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
