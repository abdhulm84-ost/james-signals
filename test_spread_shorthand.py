"""test_spread_shorthand.py — verb-less spread shorthand parses (D47).

James writes "7420C/7435C bear calls" and "7400/7410 bull puts" with NO
sell/buy verbs and sometimes no letters at all — the side word fixes the
right and the actions by convention (bear = sell lower / buy higher, bull =
the inverse). Before this, the scanner either found nothing (letter-less) or
defaulted both legs to BUY (verb-less). Permanent grammar fix: no LLM needed
for shapes a grammar can decide.
Run: python test_spread_shorthand.py   (exit 0 = green)
"""
import james_pipe as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


def sig(legs):
    return [(l["action"], l["strike"], l["right"]) for l in legs]


# the original 4062 message: no verbs, letters present
L = jp.text_legs("You can also get 7420C/7435C bear calls for today")
check("7420C/7435C bear calls -> sell 7420C / buy 7435C",
      sig(L) == [("sell", 7420.0, "call"), ("buy", 7435.0, "call")], str(sig(L)))

# letter-less pair: the side word supplies the right (sell leg first — the
# text_spread contract, matching the lotto fixture)
L = jp.text_legs("Take gains on 7400/7410 bull puts when you see in green")
check("7400/7410 bull puts -> sell 7410P / buy 7400P",
      sig(L) == [("sell", 7410.0, "put"), ("buy", 7400.0, "put")], str(sig(L)))

# words BEFORE the pair must NOT bind: "Bear calls 7700/7710 from yesterday"
# is a hold inventory (3904), and inventing a spread from it is a wrong trade.
L = jp.text_legs("the bear call spread 7415/7425 is ready")
check("words before the pair are conservative (no parse)", L == [], str(sig(L)))

# debit pairs invert (sell first)
L = jp.text_legs("get 7400/7410 bull calls at the open")
check("bull calls -> sell 7410C / buy 7400C (debit pair)",
      sig(L) == [("sell", 7410.0, "call"), ("buy", 7400.0, "call")], str(sig(L)))

L = jp.text_legs("7550/7560 bear puts for today")
check("bear puts -> sell 7550P / buy 7560P (debit pair)",
      sig(L) == [("sell", 7550.0, "put"), ("buy", 7560.0, "put")], str(sig(L)))

# regression: the verb form is unchanged (and wins over nothing else)
L = jp.text_legs("Sell SPX 7735C n Buy 7745C n collect $550 for today")
check("verb form unchanged: sell 7735C / buy 7745C",
      sig(L) == [("sell", 7735.0, "call"), ("buy", 7745.0, "call")], str(sig(L)))

# regression: the C/P straddle expansion is unchanged
L = jp.text_legs("Sell 7725C/P, Buy 7715P n 7735C")
check("straddle expansion unchanged (4 legs)",
      len(L) == 4 and ("sell", 7725.0, "put") in sig(L) and ("sell", 7725.0, "call") in sig(L),
      str(sig(L)))

# a bare pair with NO side word stays unparseable (honest park, no guessing)
L = jp.text_legs("levels 7400/7410 held yesterday")
check("pair without a side word is not guessed", L == [], str(sig(L)))

# a DATE near side words must not read as a strike pair
L = jp.text_legs("Bear calls for 11/3 held all day")
check("date 11/3 is not a strike pair", L == [], str(sig(L)))

# ---- the same shapes on the ENTRY path (text_spread/text_struct) ——
# this is what actually routes trades; text_legs alone is not enough.
L = jp.text_spread("You can also get 7420C/7435C bear calls for today")[0]
check("entry path: 7420C/7435C bear calls -> sell 7420C / buy 7435C",
      sig(L) == [("sell", 7420.0, "call"), ("buy", 7435.0, "call")], str(sig(L)))

L = jp.text_spread("Take gains on 7400/7410 bull puts when you see in green")[0]
check("entry path: 7400/7410 bull puts -> sell 7410P / buy 7400P",
      sig(L) == [("sell", 7410.0, "put"), ("buy", 7400.0, "put")], str(sig(L)))

L = jp.text_spread("7550/7560 bear puts for today")[0]
check("entry path: 7550/7560 bear puts -> sell 7550P / buy 7560P",
      sig(L) == [("sell", 7550.0, "put"), ("buy", 7560.0, "put")], str(sig(L)))

L = jp.text_spread("the bear call spread 7415/7425 is ready")[0]
check("entry path: words before the pair stay unparsed (hold-inventory safe)",
      L == [], str(sig(L)))

# D48 regression: a NARRATIVE's bare numbers must never become legs.
# 3861: 'we got Sell in 15m so 7600 and 7590 is target' once produced
# sell 15C/7600C/7590C and tried to place them.
_legs, _flags = jp.text_spread("Price broke R8 and we got Sell in 15m so 7600 and 7590 is target")
check("narrative bare numbers are not traded legs",
      not _legs or all(l["right"] is None for l in _legs), str(sig(_legs)))
_ls, _fl, _cr = jp.text_struct("Price broke R8 and we got Sell in 15m so 7600 and 7590 is target")
check("narrative flags unknown_right (routing refuses it)",
      "unknown_right" in _fl or not _ls, f"flags={_fl}")

# regression: the documented 2-leg debit default survives
L = jp.text_spread("Read this as Sell 7705 n buy 7715 for today")[0]
check("Read-this debit vertical still defaults to calls",
      sig(L) == [("sell", 7705.0, "call"), ("buy", 7715.0, "call")], str(sig(L)))

# D48: a HOLD re-statement must not open a TRADE (3904 class). The words-before
# form stays unparsed; the words-after form still parses for direction
# inference (the pinned "We are holding 7640/7630 Bull Puts" behavior).
jp.save_state = lambda st: None
jp.push = lambda t, s: None
jp.ledger_row = lambda r: None
_P = []
jp.park_signal = lambda c, m, t, w: _P.append(w)


class _FE:
    def place(self, rec):
        _P.append("PLACED:" + rec.underlying)

        class R:
            ok = True
            order_id = "T"
            venue = "tradier"
            reason = "s"
            fill_price = None
        return R()

    def order_fill(self, o):
        return "filled", 1.0

    def cancel(self, o):
        return True, "x"

    def close(self, t, limit_price=None, qty=None):
        return True, "x"


jp._TRADIER_INST = _FE()
jp.hermes_parse = lambda text, photo=None: {
    "instrument": "SPX", "direction": "short", "action": "entry", "entry": None,
    "stop": None, "targets": [], "confidence": 50, "horizon": "",
    "rationale": "", "trim": None, "spread": None}
_L = jp.text_spread("We are holding 7640/7630 Bull Puts")[0]
check("words-after hold form still parses (direction inference)",
      sig(_L) == [("sell", 7640.0, "put"), ("buy", 7630.0, "put")], str(sig(_L)))
_P.clear()
import datetime as _d
jp.us_today = lambda: _d.date(2026, 11, 4)
jp.us_session_for_today = lambda: _d.date(2026, 11, 4)
jp._parse_trade("Still holding Debit Puts and Bear calls 7700/7710 from yesterday",
                "3904", None, {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}, 0)
check("3904 hold inventory is not traded",
      not any(p.startswith("PLACED") for p in _P), str(_P))

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
