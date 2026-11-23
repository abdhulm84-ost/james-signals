"""test_invalid_card.py — an INVALID card must report, not crash.

validate_card returns norm=None for an invalid card, but _parse_trade
dereferenced it straight away. msgs 3419/3423 ("entry signal but no
instrument") died with AttributeError instead of surfacing INVALID.
Run: python test_invalid_card.py   (exit 0 = green)
"""
import james_pipe as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


jp.save_state = lambda st: None
jp.push = lambda t, s: None
jp.ledger_row = lambda r: None
jp.park_signal = lambda c, m, t, w: None
jp._listed = lambda *a: (True, False, None)
jp.spread_credit_mid = lambda u, e, l: None
jp._under_quote = lambda u: 7675.0
jp.bbc = lambda: type("B", (), {"get_quote": staticmethod(lambda i: 7675.0)})()

# a card that fails validation: entry with no instrument and nothing in the text
jp.hermes_parse = lambda t, photo=None: {
    "instrument": "", "direction": "long", "action": "entry", "entry": None,
    "stop": None, "targets": [], "confidence": 50, "horizon": "",
    "rationale": "", "trim": None, "spread": None, "option": None}

EVENTS = []
jp.ledger_row = lambda r: EVENTS.append(r.get("event"))

try:
    jp.process_message("If Price goes to the top, we can enter Bear calls again",
                       "3419", 0, None,
                       {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0})
    crashed = False
except Exception as e:
    crashed = True
    print("   crash:", type(e).__name__, e)

check("invalid card does not crash", not crashed)
check("invalid card is recorded as INVALID", "INVALID" in EVENTS, str(EVENTS))

# validate_card itself still returns None for a bad card
ok, why, norm = jp.validate_card({"direction": "sideways"}, "x")
check("bad direction -> no norm", ok is False and norm is None, f"{ok} {norm}")

print(("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
raise SystemExit(0 if not FAILS else 1)
