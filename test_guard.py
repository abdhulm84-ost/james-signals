"""test_guard.py — far-strike guard (IWM 270P against a 245 spot, msg 3653).

A strike that far from spot is a different trade from the one the message
reads as. Short premium parks; lottos are exempt; near strikes place.
Run: python test_guard.py   (exit 0 = green)
"""
import james_pipe as jp

import datetime as _dt
_FUT = jp.us_today() + _dt.timedelta(days=3)
_FUT_ISO = _FUT.isoformat()
_FUT_TXT = _FUT.strftime("%m/%d")

FAILS = []
PARKS = []
PLACED = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


jp.save_state = lambda st: None
jp.push = lambda t, s: None
jp.ledger_row = lambda r: None
jp.park_signal = lambda card, mid, text, why: PARKS.append(why)
jp._listed = lambda u, e, r, k: (True, True, None)
# no chain calls from tests: the D18 chain-mid repair made LIVE Tradier
# lookups and "repaired" the fixture credit to 0.895-of-width, which — after
# the US close, where "for today" rolls to the next session and dte=3 —
# skipped the guard's quality condition and let the no-spot fixture place.
# With the credit unperturbed (4.55 on 10 wide = 0.455 < 0.55) the guard
# enters at ANY clock.
jp.spread_credit_mid = lambda u, e, l: None
SPOT = {"v": 245.0}
jp._under_quote = lambda u: SPOT["v"]


class _F:
    def place(self, rec):
        PLACED.append(rec)

        class R:
            ok = True
            order_id = "T"
            venue = "tradier"
            reason = "stub"
            fill_price = None
        return R()

    def order_fill(self, oid):
        return "filled", 1.0

    def cancel(self, oid):
        return True, "ok"


jp._TRADIER_INST = _F()

check("28% away is far", jp.strike_far(93.8, 120)[0])
check("10% away is not (3653's 270P against 245)",
      not jp.strike_far(245, 270)[0])
check("0.7% away is near", not jp.strike_far(245, 246.7)[0])


def single(strike, lotto=False):
    PLACED.clear(); PARKS.clear()
    jp.hermes_parse = lambda text, photo=None: {
        "instrument": "IWM", "direction": "long", "action": "entry", "entry": 1.0,
        "stop": None, "targets": [], "confidence": 55, "horizon": "",
        "rationale": "x", "trim": None, "spread": None,
        "option": {"underlying": "IWM", "strike": strike, "right": "put",
                   "expiry": _FUT_ISO}}
    jp._parse_trade(("BUY IWM lotto %dP at 1.0 for _FUT_TXT" % strike) if lotto
                    else ("BUY IWM %dP at 1.0 for _FUT_TXT" % strike), "1", None,
                    {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}, 0)


# a LONG single may sit far out: its risk is the premium, so distance does not
# change the bet (4033 IONQ 29C was 35% away and must trade)
single(300)                                    # 22% away from 245
check("far LONG single places", bool(PLACED) and not PARKS, str(PARKS))

# a naked SHORT far out does park — assignment risk, not premium risk
PLACED.clear(); PARKS.clear()
jp.hermes_parse = lambda text, photo=None: {
    "instrument": "IWM", "direction": "short", "action": "entry", "entry": 1.0,
    "stop": None, "targets": [], "confidence": 55, "horizon": "",
    "rationale": "x", "trim": None, "spread": None,
    "option": {"underlying": "IWM", "strike": 200, "right": "put",
               "expiry": _FUT_ISO}}
jp._parse_trade("Selling IWM 200P at 1.0 for _FUT_TXT", "2", None,
                {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}, 0)
check("naked short single never reaches the guard (risk gate owns it)",
      not PLACED, str(PLACED))

single(150)                                    # 39% away, long
check("39% OTM long still trades (premium is the risk)", bool(PLACED) and not PARKS,
      str(PARKS))
single(250)
check("near single places", PLACED and not PARKS, str(PARKS))
single(300, lotto=True)
check("far lotto is exempt", PLACED and not PARKS, str(PARKS))

# multileg: the SHORT strike is what matters
PLACED.clear(); PARKS.clear()
wide = {"instrument": "IWM", "direction": "short", "entry": 0.5,
        "spread": {"type": "credit", "legs": [
            {"action": "sell", "strike": 300.0, "right": "put"},
            {"action": "buy", "strike": 275.0, "right": "put"}]},
        "confidence": 55, "horizon": "", "rationale": "x"}
jp.open_multileg_tradier(wide, "Sell IWM 300P n Buy 295P n collect $50 for _FUT_TXT", "2",
                         {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0})
check("far short strike parks the spread", not PLACED and PARKS, str(PARKS))


# ---- no spot at all: the guard must PARK, not guess (msg 4055) ----
jp._under_quote = lambda u: None
PARKS.clear(); PLACED.clear()
jp.open_multileg_tradier({"instrument": "SPXW", "direction": "short", "entry": 5.5,
                          "spread": None, "confidence": 60, "horizon": "day",
                          "rationale": "x", "qty": 1},
                         "Sell 7415C n Buy 7425C n collect $455 to $460 for today",
                         "995", {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0})
check("no spot -> parked, not traded", bool(PARKS) and not PLACED, f"{PARKS} {len(PLACED)}")

print(("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
raise SystemExit(0 if not FAILS else 1)
