"""test_route_4033.py — end-to-end routes for msg 4033 and 4062 ('buy IONQ 29C at 0.8').

The brain returned NO option and NO instrument, so the message fell to the
equity path and logged 'no symbol for '. The contract is in the text; the
router must rebuild it and open the option.
Run: python test_route_4033.py   (exit 0 = green)
"""
import time
import datetime
import james_pipe as jp

FAILS = []
PLACED = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


jp.save_state = lambda st: None
jp.push = lambda t, s: None
jp.ledger_row = lambda r: None
jp.park_signal = lambda c, m, t, w: None
jp._listed = lambda *a: (True, False, None)
_SPOT = {"IONQ": 45.0, "SPXW": 7415.0, "SPX500": 7415.0, "SPX": 7415.0}
jp._under_quote = lambda u: _SPOT.get(str(u or "").upper())
jp.bbc = lambda: type("B", (), {"get_quote": staticmethod(lambda i: 7415.0)})()
jp.opt_quote = lambda occ, ttl=5: 0.8


class _F:
    def place(self, rec):
        PLACED.append(rec)

        class R:
            ok = True
            order_id = "TESTORDER"
            venue = "tradier"
            reason = "stub"
            fill_price = None
        return R()


jp._TRADIER_INST = _F()
jp.tiger = lambda: (None, type("T", (), {
    "buy_to_open": lambda *a, **k: type("R", (), {"ok": True, "order_id": "T",
                                                  "venue": "tradier", "reason": "s"})(),
    "get_option_briefs": lambda *a, **k: None})(), None)


def brain_empty(text, photo=None):
    """Exactly what Hermes did on 4033: nothing useful."""
    return {"instrument": "", "direction": "flat", "action": "none", "entry": None,
            "stop": None, "targets": [], "confidence": 50, "horizon": "",
            "rationale": "", "trim": None, "spread": None, "option": None}


jp.hermes_parse = brain_empty

# D40 (clock family): the signal's date must stay future — "for 11/20" went
# stale on 11-21, the expiry guard parked it, and nothing opened (4 FAILs).
# The OCC assertion follows the same computed date.
_FUT = jp.us_today() + datetime.timedelta(days=7)
TEXT = (f"buy  IONQ   29C at  0.8 for  {_FUT.month}/{_FUT.day} "
        f"- Targets $1/1.3/1.7 SL 0.4 -MK")
st = {"open": {}, "closed": {}, "parse_retry": {}, "alerts_today": 0, "seen_msg": {}}
jp.process_message(TEXT, "4033", time.time(), None, st)

pos = list(st.get("open", {}).values())
check("an option position opened", len(pos) == 1, str([p.get("instrument") for p in pos]))
check("the contract is IONQ 29C",
      pos and pos[0].get("opt_id", "").replace(" ", "")
      == f"IONQ{_FUT.strftime('%y%m%d')}C00029000",
      str(pos and pos[0].get("opt_id")))
check("direction is long (a buy)", pos and pos[0].get("direction") == "long",
      str(pos and pos[0].get("direction")))
check("an order was actually sent", bool(PLACED), str(PLACED))

# ---- 4062: "You can also get 7420C/7435C bear calls" (no verb, no ticker) ----
class _F2(_F):
    def place(self, rec):
        OUT.append([(l.strike, l.action) for l in rec.legs])
        return super().place(rec)


OUT = []
jp._TRADIER_INST = _F2()
st2 = {"open": {}, "closed": {}, "parse_retry": {}, "alerts_today": 0, "seen_msg": {}}
jp.process_message("You can also get 7420C/7435C bear calls for today", "4062",
                   time.time(), None, st2)
check("4062 opens a structure", bool(st2.get("open")), str(list(st2.get("open", {}))))
check("4062 books the bear call (sell lower, buy higher)",
      OUT and OUT[0] == [(7420.0, "sell"), (7435.0, "buy")], str(OUT))

print(("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
raise SystemExit(0 if not FAILS else 1)
