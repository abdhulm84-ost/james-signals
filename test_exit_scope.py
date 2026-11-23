"""test_exit_scope.py — the exit scope resolver (msgs 3910/3917).

Two defects: (a) "Debit Puts" was read as the INSTRUMENT 'DEBIT', so the exit
found "no position in DEBIT"; (b) a bare family word swept every historical
row in the book instead of the named-strike position.
Run: python test_exit_scope.py   (exit 0 = green)
"""
import james_pipe as jp

FAILS = []
CLOSED = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


jp.save_state = lambda st: None
jp.push = lambda t, s: None
jp.ledger_row = lambda r: None
jp.close_trade = lambda t, px, why, st: CLOSED.append(t["id"])
TODAY = jp.us_session_for_today().isoformat()
jp._under_quote = lambda u: 7704.0
jp.bbc = lambda: type("B", (), {"get_quote": staticmethod(lambda i: 7704.0)})()

check("DEBIT is not an instrument", jp.scope_instrument(
    "Taking gains on today's 7700/7715 Bear calls and Debit Puts") != "DEBIT")
check("the strikes are still found", jp.text_strikes(
    "Taking gains on today's 7700/7715 Bear calls and Debit Puts") == [7700.0, 7715.0])


def book():
    return {"seen_msg": {}, "closed": {}, "alerts_today": 0, "open": {
        "T1": {"id": "T1", "instrument": "SPXW", "symbol": "SPXW",
               "executor": "tradier", "status": "open_tradier",
               "direction": "short", "qty": 1, "entry": 4.75,
               "expiry": TODAY,
               "legs": [[7700.0, "call", "sell"], [7710.0, "call", "buy"]]}}}


CLOSED.clear()
st = book()
jp.handle_exit_update("exit", {"instrument": "SPX", "direction": "short"}, "3917", st,
                      "Taking gains on today's 7700/7715 Bear calls and Debit Puts")
check("Debit Puts exit closes the held spread", CLOSED == ["T1"], str(CLOSED))

# a family-only exit must not sweep: a stale unrelated row stays untouched
CLOSED.clear()
st2 = book()
st2["open"]["STALE"] = {"id": "STALE", "instrument": "SPXW", "symbol": "SPXW",
                        "executor": "tradier", "status": "open_tradier",
                        "direction": "short", "qty": 1, "entry": 9.0,
                        "expiry": TODAY,
                        "legs": [[7000.0, "call", "sell"], [7010.0, "call", "buy"]]}
jp.handle_exit_update("exit", {"instrument": "SPX"}, "3910", st2,
                      "30% on SPX 7700/7710 Bear calls, Pl book gains")
check("named strikes do not sweep the book", "STALE" not in CLOSED, str(CLOSED))

print(("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
raise SystemExit(0 if not FAILS else 1)
