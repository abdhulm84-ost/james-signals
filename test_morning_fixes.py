"""test_morning_fixes.py — three defects from the 2026-11-08 review.

4062 'You can also get 7420C/7435C bear calls' parsed to zero legs (the no-verb
form only accepted bare strikes) and became NARRATIVE. 4033 'buy IONQ 29C at
0.8' had no card option and no ticker, so it fell to the equity path and logged
'no symbol for '. TL4055 sat exactly on credit/width 0.55 and kept full size.
Run: python test_morning_fixes.py   (exit 0 = green)
"""
import james_pipe as jp

FAILS = []
PLACED = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


def legs(t):
    return jp.text_spread(t)[0]


# ---- 4062: alternate-structure wording, rights written after the strike ----
L = legs("You can also get 7420C/7435C bear calls for today")
check("4062: two legs parsed", len(L) == 2, str(L))
check("4062: bear call sells the LOWER strike", L and L[0]["action"] == "sell"
      and L[0]["strike"] == 7420.0, str(L))
check("4062: buys the higher strike", L and L[1]["action"] == "buy"
      and L[1]["strike"] == 7435.0, str(L))
check("4062: no unknown right", L and all(x["right"] == "call" for x in L), str(L))
check("4062: no inconsistency flag", "inconsistent" not in jp.text_struct(
    "You can also get 7420C/7435C bear calls for today")[1])
check("4062: routed this far today would now reach the multileg executor",
      len(jp.text_struct("You can also get 7420C/7435C bear calls for today")[0]) >= 2)

# bare-strike no-verb form still works (lotto 50/60 bull puts)
L2 = legs("Spx lotto 50/60 bull puts for today- take small")
check("regression: bare no-verb lotto still sells the higher strike",
      L2 and L2[0]["strike"] == 60.0 and L2[0]["action"] == "sell", str(L2))

# ---- 4033: single contract rebuilt from text ----
c = jp.text_option_card("buy  IONQ   29C at  0.8 for  11/6\nTargets $1/1.3/1.7\nSL 0.4")
check("4033: contract rebuilt", bool(c), str(c))
check("4033: underlying", c and c["underlying"] == "IONQ", str(c))
check("4033: strike/right", c and c["strike"] == 29.0 and c["right"] == "call", str(c))
check("4033: premium", c and c["entry"] == 0.8, str(c))
# D40 (clock family): assert the MONTH-DAY the message named — the resolver's
# YEAR rolls when today crosses the fixture's asof boundary (2026-11-06 went
# to 2027 on 11-21), which is resolver policy, not what this test is about.
check("4033: expiry", c and c["expiry"][5:] == "11-06", str(c))
check("single card has no false positives on a level line",
      jp.text_option_card("7400 and 7430 targets for SPX") is None,
      str(jp.text_option_card("7400 and 7430 targets for SPX")))
check("single card refuses a spread",
      jp.text_option_card("Sell 7410P n Buy 7400P n collect $395") is None)

# ---- 4055: quality halving bites AT the 0.55 boundary ----
st = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
jp.save_state = lambda s: None
jp.push = lambda t, s: None
jp.ledger_row = lambda r: None
jp.bbc = lambda: type("B", (), {"get_quote": staticmethod(lambda inst: 7545.0)})()
jp.spread_credit_mid = lambda u, e, l: None   # text credits: no chain check in tests


class _FakeTradier:
    def place(self, rec):
        PLACED.append(rec)

        class R:
            ok = True
            order_id = "TESTORDER"
            venue = "tradier"
            reason = "stub"
            fill_price = None
        return R()


jp._TRADIER_INST = _FakeTradier()


def place(text, qty, mid="901"):
    PLACED.clear()
    st2 = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
    norm = {"instrument": "SPX", "direction": "short", "entry": None, "spread": None,
            "confidence": 60, "horizon": "day", "rationale": "x", "qty": qty}
    jp.open_multileg_tradier(norm, text, mid, st2)
    return PLACED[0].qty if PLACED else None


q55 = place("Sell 7560C n Buy 7570C n collect $550 to $560 for today", 6)
# D72: qty 1 is the hard ceiling until a band graduates, so the
# quality-halving path also lands on 1 (3 clamped to 1).
check("0.55 credit/width halves qty 6 -> 3, capped at 1", q55 == 1, str(q55))
q56 = place("Sell 7560C n Buy 7570C n collect $560 to $570 for today", 6, "902")
check("0.56 keeps full size but capped at 1", q56 == 1, str(q56))

print(("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
raise SystemExit(0 if not FAILS else 1)
