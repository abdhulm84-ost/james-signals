"""test_retro_fixes.py — two things the Sunday review missed on the open book.

The CHTR row carried WPM's 140P contract while its own text called a 150C, and it
was read merely as a price gap. And both open positions rode with no target.
Run: python test_retro_fixes.py   (exit 0 = green)
"""
import datetime
import james_pipe as jp

FAILS = []
PUSH = []
# D40 (clock family): pin expiries + the signal's date to the clock under
# test — "2026-11-20" went stale on 11-21 and open_option_trade refused
# every placement (both checks read None).
_EXPIRY = (jp.us_today() + datetime.timedelta(days=2)).isoformat()
_EXPIRY_MD = f"{int(_EXPIRY[5:7])}/{int(_EXPIRY[8:])}"


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


CARD = {"instrument": "CHTR", "direction": "long", "action": "entry", "entry": 24.8,
        "option": {"underlying": "CHTR", "strike": 140.0, "right": "put",
                   "expiry": _EXPIRY}}

check("crossed card is flagged",
      jp.validate_card(dict(CARD), "Bullish for WPM - Sell 140P n Buy 135P")[2]
      .get("crossed_card") is True)
check("own ticker in text is not a crossed card",
      not jp.validate_card(dict(CARD), "CHTR 150C at 1.65")[2].get("crossed_card"))
check("no ticker in text is not a crossed card",
      not jp.validate_card(dict(CARD), "buy 150C at 1.65")[2].get("crossed_card"))

# ---- 2R target when a single states none ----
jp.save_state = lambda st: None
jp.push = lambda t, s: PUSH.append(t)
jp.ledger_row = lambda r: None
jp._under_quote = lambda u: 100.0
jp.opt_quote = lambda occ, ttl=5: 2.0
jp._listed = lambda *a: (True, True, None)
class _Fake:
    def place(self, rec):
        class R:
            ok = True
            order_id = "TESTORDER"
            venue = "tradier"
            reason = "stub"
            fill_price = None
        return R()


jp._TRADIER_INST = _Fake()  # never place a real order from this test
jp.tiger = lambda: (None, type("T", (), {
    "buy_to_open": lambda *a, **k: type("R", (), {"ok": True, "order_id": "T",
                                                  "venue": "tradier", "reason": "s"})()})(), None)

class _Fake:
    def place(self, rec):
        class R:
            ok = True
            order_id = "TESTORDER"
            venue = "tradier"
            reason = "stub"
            fill_price = None
        return R()


norm = {"instrument": "XYZ", "direction": "long", "action": "entry", "entry": 2.0,
        "stop": None, "targets": [], "confidence": 50, "horizon": "day",
        "rationale": "x", "qty": 1,
        "option": {"underlying": "XYZ", "strike": 100.0, "right": "call",
                   "expiry": _EXPIRY}}
st = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
jp.open_option_trade(norm, "r-2r", st, f"buy XYZ 100C at 2.0 for {_EXPIRY_MD}")
t = next(iter(st["open"].values()), None)
check("a placed single gets a 2R target", t and t["targets"] == [3.6],
      str(t and t["targets"]))
check("stop follows the 40% single policy", t and t["stop"] == 1.2, str(t and t["stop"]))

print(("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
raise SystemExit(0 if not FAILS else 1)
