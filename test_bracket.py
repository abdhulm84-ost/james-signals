"""test_bracket.py — conditional entry brackets from a levels view (msg 3657).

'Bearish below 28710 / bullish above 28880' + an S/R ladder is a plan, not an
order. It is armed, fires ONCE on a level cross this session, and expires.
Run: python test_bracket.py   (exit 0 = green)
"""
import james_pipe as jp

FAILS = []
FIRED = []
ROWS = []
PUSHED = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


jp.save_state = lambda st: None
jp.ledger_row = lambda r: ROWS.append(r["event"])
jp.push = lambda t, s: PUSHED.append(t)
jp.open_trade_bb = lambda card, mid, st: FIRED.append(
    (card["direction"], card.get("stop"), card.get("targets")))

TXT = ("Nasdaq 100 E-mini futures:\nnow $28800\nS1:$28620\nR1:$28960\nR2: $29050\n"
       "View: bearish below 28710\nbullish above $28880")

check("bracket parser sees both sides and the ladder",
      jp.text_bracket(TXT) == {"up": 28880.0, "down": 28710.0,
                               "sr": {"S1": 28620.0, "R1": 28960.0, "R2": 29050.0}},
      str(jp.text_bracket(TXT)))

check("an S/R pair parses as a bracket too (3658)",
      jp.text_bracket("7630 is Strong Support and 7670 is Strong Resistance")
      == {"up": 7670.0, "down": 7630.0, "sr": {}},
      str(jp.text_bracket("7630 is Strong Support and 7670 is Strong Resistance")))
check("one-sided support alone makes no bracket",
      jp.text_bracket("7630 is strong support only") is None)

Q = {"v": 28900.0}
jp._under_quote = lambda u: Q["v"]
jp.hermes_parse = lambda text, photo=None: {
    "instrument": "NDX100", "direction": "flat", "action": "none", "entry": None,
    "stop": None, "targets": [], "confidence": 60, "horizon": "",
    "rationale": "levels", "trim": None, "spread": None, "option": None}

st = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0, "armed": {}}
jp._parse_trade(TXT, "3657", None, st, 0)
check("the plan is armed, not traded", list(st["armed"]) == ["AR3657"] and not FIRED)
check("arming is announced", any("ARMED" in p for p in PUSHED))

jp.manage_once(st)
check("cross above fires a LONG", FIRED and FIRED[0][0] == "long", str(FIRED))
check("long stop is the opposite trigger (28710)", FIRED and FIRED[0][1] == 28710.0, str(FIRED))
check("targets are the resistances above",
      FIRED and FIRED[0][2] == [28960.0, 29050.0], str(FIRED))
FIRED.clear()
jp.manage_once(st)
check("one shot only", not FIRED, str(FIRED))

# short side: below the down level
st = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0, "armed": {}}
Q["v"] = 28700.0
jp._parse_trade(TXT, "3658", None, st, 0)
jp.manage_once(st)
check("cross below fires a SHORT", FIRED and FIRED[0][0] == "short", str(FIRED))
check("short stop is the opposite trigger (28880)", FIRED and FIRED[0][1] == 28880.0, str(FIRED))
check("short targets are the supports", FIRED and FIRED[0][2] == [28620.0], str(FIRED))

# expiry
st["armed"]["X"] = {"id": "X", "msg_id": 9, "instrument": "NDX100", "up": 1.0,
                    "down": None, "sr": {}, "session": "2020-01-01", "opened": jp.now()}
jp.manage_once(st)
check("stale session plan expires", "X" not in st["armed"], str(st["armed"]))
check("expiry recorded", "BRACKET_EXPIRED" in ROWS)

print(f"\n{len(FAILS)} FAIL(S)" if FAILS else "\nALL PASS")
raise SystemExit(1 if FAILS else 0)
