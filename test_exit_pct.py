"""test_exit_pct.py — '<n>% on <structure>' standing trim (msg 3639 / D121).

James writes "30% on bull puts" as a TAKE-PARTIAL. The parser used to read any
'% on' as a full close, so a 245% winner was booked out entirely.

D121 (2026-11-19) separates the two readings of the same words:
  * a BARE '<n>% on <structure>' with NO action verb is a STATUS COMMENT and
    does NOT route to the exit path (msg 4496 '35% on bull puts' full-closed a
    winner at -$40; msg 3639's own ledger row is NARRATIVE, never traded)
  * a bare % WITH an explicit action verb ('35% on SPX Bear calls, Booking
    gains') still routes, and then honours the PARTIAL (0.3 -> 2 of 6)
So the parse directive is unchanged (0.3); only the routing is tightened.
Run: python test_exit_pct.py   (exit 0 = green)
"""
import james_pipe as jp

FAILS = []
CLOSED = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


def directive(text):
    return jp.parse_exit_directive({}, text)[2]


check("standalone '30% on bull puts' -> 0.3 (partial, never a full close)",
      directive("30% on bull puts") == 0.3,
      str(directive("30% on bull puts")))
# D121: a BARE % is a status -> does not route to the exit path
check("bare '30% on bull puts' does NOT route to exit (D121 status)",
      not jp.exit_command_wins("30% on bull puts"))
# D121: a bare % WITH an explicit action verb still routes
check("'35% on SPX Bear calls, Booking gains' still routes (D121 verb)",
      jp.exit_command_wins("35% on SPX Bear calls, Booking gains"),
      str(jp.exit_command_wins("35% on SPX Bear calls, Booking gains")))
check("'% on' + booking verb stays a FULL close",
      directive("95% on QQQ bear calls, pl book gains") == 1.0)
check("'20% up' progress mark is not an exit", not jp.exit_command_wins("20% up as of now"))

# --- execution: 30% of 6 contracts = 2 closed, 4 keep -------------------
jp.save_state = lambda st: None
jp.push = lambda t, s: None
jp.ledger_row = lambda r: None
jp.quote_for = lambda t: 3.45


def _close(t, px, why, st):
    CLOSED.append((t["id"], t.get("close_qty")))
    t["status"] = "close_pending"


jp.close_trade = _close


def book(instrument="SPXW", right="put"):
    return {"seen_msg": {}, "closed": {}, "alerts_today": 0, "open": {
        "TL1": {"id": "TL1", "instrument": instrument, "symbol": instrument,
                "direction": "short", "qty": 6, "entry": 4.4,
                "expiry": "2026-11-06", "status": "open_tradier", "executor": "tradier",
                "legs": [[7635.0, right, "sell"], [7625.0, right, "buy"]],
                "stop": None, "targets": []}}}


card = {"instrument": "SPX500", "direction": "long", "action": "exit", "entry": None,
        "stop": None, "targets": [], "confidence": 50, "horizon": "", "rationale": "x",
        "trim": None, "spread": None, "option": None}

CLOSED.clear()
jp.handle_exit_update("exit", card, "3639", book(), "30% on bull puts")
check("30% of 6 -> close_qty 2", CLOSED and CLOSED[0][1] == 2, str(CLOSED))

CLOSED.clear()
jp.handle_exit_update("exit", card, "3639b", book("SPXW", "call"), "30% on bull puts")
check("a CALL position is untouched by 'bull puts'", not CLOSED, str(CLOSED))

# --- 3661-3663: "Take gains with X" scopes on the ticker after 'with' ----
check("ticker after 'with' is the scope", jp.scope_instrument("Take gains with RDDT") == "RDDT")
check("a structure word still wins first",
      jp.scope_instrument("Take gains with GD Bull Puts") == "GD")
check("'all trades' is not a ticker", jp.scope_instrument("Take gains with all trades") is None)

CLOSED.clear()
jp.close_trade = _close
_wb = book("RDDT", "call")
_wb["open"]["TL1"]["legs"] = [[120.0, "call", "sell"], [125.0, "call", "buy"]]
_wb["open"]["SPX"] = {"id": "SPX", "instrument": "SPXW", "symbol": "SPXW",
                      "direction": "short", "qty": 2, "entry": 3.0,
                      "expiry": "2026-10-30", "status": "open_tradier",
                      "executor": "tradier", "legs": [[7700.0, "put", "sell"],
                                                       [7690.0, "put", "buy"]],
                      "stop": None, "targets": []}
jp.handle_exit_update("exit", card, "3662", _wb, "Take gains with RDDT")
check("the named ticker closes, a wrong brain instrument does not hijack it",
      [c[0] for c in CLOSED] == ["TL1"], str(CLOSED))

# --- 3644: screenshot strikes carry thousands commas ---------------------
check("comma strike pair parses",
      jp.text_strikes("SPXW 7,635 / 7,625 Puts") == [7625.0, 7635.0])

# --- the SCREENSHOT's own position scopes an unnamed exit ----------------
img_card = dict(card, img_strikes=[7635.0, 7625.0])


def two_books():
    st = book()
    st["open"]["TL2"] = {"id": "TL2", "instrument": "QQQ", "symbol": "QQQ",
                         "direction": "short", "qty": 4, "entry": 0.9,
                         "expiry": "2026-10-30", "status": "open_tradier",
                         "executor": "tradier",
                         "legs": [[656.0, "put", "sell"], [654.0, "put", "buy"]],
                         "stop": None, "targets": []}
    return st


CLOSED.clear()
jp.handle_exit_update("exit", img_card, "3644", two_books(), "pl book gains")
check("screenshot position scopes the close",
      [c[0] for c in CLOSED] == ["TL1"], str(CLOSED))

CLOSED.clear()
jp.handle_exit_update("exit", dict(card, img_strikes=[9999.0]), "3644b", two_books(),
                      "pl book gains on puts")
check("stale image strike falls back to the named family",
      [c[0] for c in CLOSED] == ["TL1", "TL2"], str(CLOSED))

CLOSED.clear()
jp.handle_exit_update("exit", dict(card, img_strikes=[9999.0]), "3644c", two_books(),
                      "pl book gains")
check("known underlying still closes when the image strike is stale",
      [c[0] for c in CLOSED] == ["TL1"], str(CLOSED))

CLOSED.clear()
no_inst = dict(card, instrument=None, img_strikes=[9999.0])
jp.handle_exit_update("exit", no_inst, "3644d", two_books(), "pl book gains")
check("no instrument, no family, stale strike -> refuses", not CLOSED, str(CLOSED))

# --- 3645-3647: premarket predictions must NOT fire exits ----------------
check("'25% drop on RDDT, ... will make 100%' is not an exit",
      not jp.exit_command_wins("25% drop on RDDT, Our 120/125 Bear calls will make "
                               "nearly 100% at Open"))
check("'QCOM close TO 177.5 ...' proximity is not a close command",
      not jp.exit_command_wins("QCOM close to 177.5 in Premarket, QCOM 177.5/182.5 "
                               "Bear calls will make nearly 100% at Open."))
check("'AN went up 3% ... Bull Puts will make nice gains' is not an exit",
      not jp.exit_command_wins("AN went up 3% in Premarket post Earnings. Bull Puts "
                               "will make nice gains at Open."))
check("a real close still fires", jp.exit_command_wins("Close QCOM 177.5/182.5 Bear calls"))
check("proximity + book gains still fires",
      jp.exit_command_wins("QCOM close to 177.5, pl book gains"))

# --- 3649: 'trim 80% and hold 20% until expiry' --------------------------
hold_book = book("SMCI", "call")
hold_book["open"]["TL1"]["legs"] = [[32.5, "call", "sell"], [37.5, "call", "buy"]]
hold_book["open"]["TL1"]["stop"] = 2.5
CLOSED.clear()
jp.handle_exit_update("exit", dict(card, instrument="SMCI"), "3649", hold_book,
                      "SMCI 32.5/37.5 Bear calls will make 100% at Open today, "
                      "Pl trim 80% and hold 20% until expiry")
check("trim 80% of 6 -> 5", CLOSED and CLOSED[0][1] == 5, str(CLOSED))
check("remainder flagged hold-to-expiry",
      hold_book["open"]["TL1"].get("hold_until_expiry") is True)

held = hold_book["open"]["TL1"]
held["status"] = "open_tradier"
CLOSED.clear()
jp.quote_for = lambda t: 3.0          # short premium moving against the stop
jp.manage_once(hold_book)
check("stop does not take the held runner", not CLOSED, str(CLOSED))

# --- no matching position: say so, do not act ----------------------------
push_backup = jp.push
PUSHED = []
jp.push = lambda t, s: PUSHED.append(t)
CLOSED.clear()
empty = book("SMCI", "call")
empty["open"] = {}
jp.handle_exit_update("exit", dict(card, instrument="SMCI"), "3650",
                      empty, "SMCI Bear calls, Pl trim 80%")
jp.push = push_backup
check("trim with nothing held closes nothing", not CLOSED, str(CLOSED))
check("trim with nothing held is surfaced",
      any("NOT HELD" in p for p in PUSHED), str(PUSHED[:1]))

print(f"\n{len(FAILS)} FAIL(S)" if FAILS else "\nALL PASS")
raise SystemExit(1 if FAILS else 0)
