#!/usr/bin/env python3
"""verify_scenarios — replay every overnight/audit scenario against the LIVE code.

Each scenario exercises the real function paths with broker/state stubs and
asserts the CORRECT handling. Run any time; nothing is placed.
"""
import james_pipe as jp
import datetime as _dt
_FUTD = jp.us_today() + _dt.timedelta(days=3)
FUT = _FUTD.isoformat()
FUT_TXT = f"{_FUTD.month}/{_FUTD.day}"

FAILS = []
def chk(name, cond, why=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  <- {why}" if why and not cond else ""))
    if not cond:
        FAILS.append(name)

jp.save_state = lambda st: None
jp._under_quote = lambda u: None
jp.push = lambda t, s: None
jp.ledger_row = lambda r: None
jp._load_book = lambda: jp.Book()
jp._save_book = lambda b: None
jp.opt_mark = lambda t: None

PLACED, CANCELS = [], []
FAKE = {"status": "filled", "fill": 3.0, "cancel_ok": True}
class _T:
    def place(self, rec):
        PLACED.append(rec)
        class R: ok = True; order_id = "T1"; venue = "tradier"; reason = "stub"; fill_price = None
        return R()
    def order_fill(self, oid): return FAKE["status"], FAKE["fill"]
    def cancel(self, oid):
        CANCELS.append(oid)
        return FAKE["cancel_ok"], ("cancelled" if FAKE["cancel_ok"] else "HTTP 500")
jp._TRADIER_INST = _T()
CLOSE = []
_REAL_CLOSE = jp.close_trade
jp.close_trade = lambda t, px, why, st: CLOSE.append(t["id"])
jp.close_option = lambda t, px, q, why, st: CLOSE.append("O:" + t["id"])
_REAL_HANDLE = jp.handle_exit_update

def st0(): return {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}

# 1. 3769 blind-close: only the named ticker closes
s = st0()
s["open"]["F"] = {"id": "F", "instrument": "FISV 52C", "symbol": "FISV",
                  "executor": "tradier", "status": "open_tradier", "direction": "long",
                  "qty": 1, "entry": 1.0, "expiry": "2026-11-06"}
s["open"]["B"] = {"id": "B", "instrument": "BLK", "symbol": "BLK",
                  "executor": "tradier", "status": "open_tradier", "direction": "short",
                  "qty": 1, "entry": 4.8}
CLOSE.clear()
_REAL_HANDLE("exit", {"instrument": None, "direction": "short"}, "3769", s,
             "Nice gains with CLX, FISV, Trim 80% on FISV")
chk("3769 trim closes FISV only", CLOSE == ["F"], str(CLOSE))

# 2. no-scope exit refuses
CLOSE.clear()
_REAL_HANDLE("exit", {"instrument": None, "direction": "short"}, "x", s, "pl book gains")
chk("no-scope exit refuses (nothing closed)", CLOSE == [], str(CLOSE))

# 3. AEHR: REJECTED at placement -> booked no-position
FAKE.update(status="rejected", fill=None)
PLACED.clear()
s2 = st0()
jp.open_option_trade({"direction": "long", "action": "entry", "entry": 5.0,
                      "stop": 2.5, "targets": [6, 7.5, 9.5], "confidence": 80,
                      "horizon": "swing", "rationale": "x",
                      "option": {"underlying": "AEHR", "strike": 60.0,
                                 "right": "call", "expiry": FUT}},
                     "3773", s2, f"buy AEHR 60C at 5 for {FUT_TXT}")
chk("AEHR rejected -> no position in open",
    len(s2["open"]) == 0 and any(k.startswith("O") for k in s2["closed"]),
    str(list(s2["open"])))

# 4. single options record their expiry (close no longer fails 'no expiry')
FAKE.update(status="filled", fill=0.56)
PLACED.clear()
s3 = st0()
jp.open_option_trade({"direction": "long", "action": "entry", "entry": 0.78,
                      "stop": 0.3, "targets": [1.2], "confidence": 60,
                      "horizon": "day", "rationale": "x",
                      "option": {"underlying": "IWM", "strike": 281.0,
                                 "right": "call", "expiry": "2026-11-03"}},
                     "3778", s3, "IWM 281C at 0.78 for today")
rec = next(iter(s3["open"].values()))
chk("single option record carries expiry",
    rec.get("expiry") == jp._signal_expiry(None, "for today"),
    str(rec.get("expiry")))

# 5. TL3765 class: exit on unfilled entry + cancel FAILS -> stays pending
FAKE.update(status="open", fill=None, cancel_ok=False)
s4 = st0()
s4["open"]["P"] = {"id": "P", "executor": "tradier", "status": "pending_entry",
                   "instrument": "SPXW", "symbol": "SPXW", "direction": "short",
                   "order_id": "O1", "entry": 3.9, "qty": 1}
_REAL_CLOSE(s4["open"]["P"], 0.0, "exit msg", s4)
chk("pending+cancel-fail stays pending (never booked closed)",
    s4["open"]["P"]["status"] == "pending_entry" and "P" in s4["open"],
    s4["open"]["P"]["status"])

# 6. empty instrument + text ticker -> SPXW
FAKE.update(status="filled", fill=4.7, cancel_ok=True)
PLACED.clear()
jp.open_multileg_tradier({"instrument": "", "direction": "short", "entry": 4.7,
                          "spread": {"type": "credit", "legs": [
                              {"action": "sell", "strike": 7590.0, "right": "put"},
                              {"action": "buy", "strike": 7580.0, "right": "put"}]},
                          "confidence": 60, "horizon": "day", "rationale": "x"},
                         "Sell SPX 7590P n Buy 7580P n collect $470 for today", "3793", st0())
chk("empty instrument -> text ticker SPXW", PLACED and PLACED[0].underlying == "SPXW",
    str(PLACED[0].underlying if PLACED else None))

# 7. debit spread books LONG
PLACED.clear()
jp.open_multileg_tradier({"instrument": "SPX", "direction": "short", "entry": 5.4,
                          "spread": {"type": "debit", "legs": [
                              {"action": "sell", "strike": 7580.0, "right": "call"},
                              {"action": "buy", "strike": 7570.0, "right": "call"}]},
                          "confidence": 60, "horizon": "day", "rationale": "x"},
                         "Sell SPX 7580C n Buy 7570C for 5.4 - Debit Spread", "3798", st0())
chk("debit books LONG", PLACED and PLACED[0].direction == "long",
    str(PLACED[0].direction if PLACED else None))

# 8. 3783 canon exit hits SPXW
CLOSE.clear()
s5 = st0()
s5["open"]["W"] = {"id": "W", "instrument": "SPXW", "symbol": "SPXW",
                   "executor": "tradier", "status": "open_tradier", "direction": "short",
                   "qty": 1, "entry": 4.4, "expiry": "2026-11-06",
                   "legs": [[7600.0, "put", "sell"], [7590.0, "put", "buy"]]}
_REAL_HANDLE("exit", {"instrument": "SPX", "direction": "flat"}, "3783", s5,
             "SPX 7572 again, pl book gains on any trades below or close to 7570")
chk("3783 SPX exit hits SPXW position", CLOSE == ["W"], str(CLOSE))

# 9. unresolved symbol -> prevalidation park, no broker call
PLACED.clear()
jp.open_multileg_tradier({"instrument": "??", "direction": "short", "entry": 0.5,
                          "spread": {"type": "credit", "legs": [
                              {"action": "sell", "strike": 62.0, "right": "call"},
                              {"action": "buy", "strike": 60.0, "right": "call"}]},
                          "confidence": 50, "horizon": "day", "rationale": "x"},
                         "Sell 62C n Buy 60C n collect $50 for today", "928", st0())
chk("unresolved symbol parked pre-call", len(PLACED) == 0)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    raise SystemExit(1)
print("ALL SCENARIOS HANDLED")
