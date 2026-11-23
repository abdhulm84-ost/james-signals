"""test_colour_arm.py — market-colour messages arm exit levels on open positions.

'SPX below 7550 now' / 'Big bounce on SPX' carry a level that walks an open
position into profit. The level is armed as an exit trigger: a held bear call
wins BELOW, a held bull put wins ABOVE. The family filter binds to calls/puts
when the text names a structure.
Run: python test_colour_arm.py   (exit 0 = green)
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


def book(**positions):
    st = {"open": {}, "closed": {}, "alerts_today": 0, "seen_msg": {}}
    for tid, (right, short_strike, buy_strike) in positions.items():
        st["open"][tid] = {
            "id": tid, "instrument": "SPXW", "status": "open_tradier",
            "direction": "short", "qty": 1, "entry": 5.0,
            "legs": [[short_strike, right, "sell"], [buy_strike, right, "buy"]]
        }
    return st


# empty book
check("empty book: no arm",
      jp.arm_colour_levels([(7550.0, "below")], "SPX below 7550 now", "3831",
                           book()) == [])

# bear call + "below" -> ARMED
st = book(C1=("call", 7555.0, 7565.0))
check("bear call + below: armed",
      jp.arm_colour_levels([(7550.0, "below")], "SPX below 7550 now", "3831", st) == ["C1"])

# bear call + "above" -> NOT armed (call spread loses above)
st2 = book(C2=("call", 7415.0, 7425.0))
check("bear call + above: not armed",
      jp.arm_colour_levels([(7400.0, "above")], "SPX above 7400 now", "3845", st2) == [])

# bull put + "above" -> ARMED
st3 = book(P3=("put", 7590.0, 7580.0))
check("bull put + above: armed",
      jp.arm_colour_levels([(7400.0, "above")], "SPX above 7400 now", "3845", st3) == ["P3"])

# bull put + "below" -> NOT armed
check("bull put + below: not armed",
      jp.arm_colour_levels([(7590.0, "below")], "SPX below 7590 now", "3890", st3) == [])

# family filter: "Bear calls making" only arms calls
st4 = book(C4=("call", 7555.0, 7565.0), P4=("put", 7350.0, 7340.0))
check("mixed book, Bear calls family, below: only call armed",
      jp.arm_colour_levels([(7550.0, "below")],
                           "SPX below 7550 now — Bear calls making", "3896", st4) == ["C4"])

# already-armed position is not re-armed
st_already = book(C5=("call", 7555.0, 7565.0))
st_already["open"]["C5"]["exit_target"] = {"level": 7540.0, "side": "below", "msg": "old",
                                            "source": "exit_ladder"}
check("already-armed: no double-arm",
      jp.arm_colour_levels([(7550.0, "below")], "SPX below 7550", "x", st_already) == [])

# the manage loop fires the armed level (test via _exit_target_hit)
t = {"instrument": "SPXW", "legs": [[7555.0, "call", "sell"], [7565.0, "call", "buy"]],
     "exit_target": {"level": 7550.0, "side": "below", "msg": "3831",
                     "source": "market_colour"}}
jp.bbc = lambda: type("B", (), {"get_quote": staticmethod(lambda i: 7549.0)})()
check("colour level fires at 7549 (below 7550)", jp._exit_target_hit(t) == (True, 7549.0))
jp.bbc = lambda: type("B", (), {"get_quote": staticmethod(lambda i: 7551.0)})()
check("colour level does not fire at 7551 (above 7550)",
      jp._exit_target_hit(t) == (False, 7551.0))

# put spread: ABOVE fires
t2 = {"instrument": "SPXW", "legs": [[7590.0, "put", "sell"], [7580.0, "put", "buy"]],
      "exit_target": {"level": 7400.0, "side": "above", "msg": "3845",
                       "source": "market_colour"}}
jp.bbc = lambda: type("B", (), {"get_quote": staticmethod(lambda i: 7401.0)})()
check("put target fires at 7401 (above 7400)", jp._exit_target_hit(t2) == (True, 7401.0))

print(("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
raise SystemExit(0 if not FAILS else 1)
