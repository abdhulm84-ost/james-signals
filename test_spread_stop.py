"""test_spread_stop.py — a resting spread stop must fire on the DEBIT TO CLOSE.

Before this, only singles and stocks were marked in manage_once; an open
vertical with a stop was never checked, so TL4003 (WPM q5, no stop) and any
future spread ran completely unprotected.
Run: python test_spread_stop.py   (exit 0 = green)
"""
import datetime
import james_pipe as jp

FAILS = []
CLOSED = []
# D40 (clock family): pin the fixture expiry to the clock under test — a
# hardcoded "2026-11-20" went stale when the US date rolled to 11-21 and
# settle_expired settled TL4003 before the stop could fire (3 FAILs).
_EXPIRY = (jp.us_today() + datetime.timedelta(days=2)).isoformat()


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


jp.save_state = lambda st: None
jp.push = lambda t, s: None
jp.ledger_row = lambda r: None
# D54: spread_mark floors at the intrinsic debit — stub the price seam too
# (tests never hit live feeds) deep OTM so the floor stays 0 for these checks
jp._under_quote = lambda u: 200.0


def _close(t, px, why, st):
    CLOSED.append((t["id"], px, why))
    t["status"] = "close_pending"


jp.close_trade = _close

# 140/135 short put vertical: sell leg trades 6.0, buy leg 1.0 -> debit 5.0
# keys follow the pinned expiry (D40): OCC carries YYMMDD
_OCCD = _EXPIRY[2:].replace("-", "")
QUOTES = {f"WPM{_OCCD}P00140000": 6.0, f"WPM{_OCCD}P00135000": 1.0}
jp.opt_quote = lambda occ, ttl=5: QUOTES.get(occ.upper())

trade = {"id": "TL4003", "instrument": "WPM", "symbol": "WPM", "direction": "short",
         "qty": 5, "entry": 3.2, "expiry": _EXPIRY, "status": "open_tradier",
         "executor": "tradier", "legs": [[140.0, "put", "sell"], [135.0, "put", "buy"]],
         "stop": 4.8, "targets": []}


def book(stop):
    t = dict(trade)
    t["stop"] = stop
    return {"seen_msg": {}, "closed": {}, "alerts_today": 0, "open": {"TL4003": t}}


check("mark = debit to close (6.0 - 1.0)", jp.spread_mark(trade) == 5.0,
      str(jp.spread_mark(trade)))

CLOSED.clear()
jp.manage_once(book(4.8))
check("debit 5.0 >= stop 4.8 closes on the stop", bool(CLOSED), str(CLOSED))
check("close reason is the stop", CLOSED and CLOSED[0][2].startswith("stop hit"),
      str(CLOSED))

CLOSED.clear()
jp.manage_once(book(6.0))
check("debit below stop stays open", not CLOSED, str(CLOSED))

CLOSED.clear()
jp.manage_once(book(None))
check("no stop -> no stop close", not CLOSED, str(CLOSED))
QUOTES.clear()
check("unquotable leg returns None", jp.spread_mark(trade) is None, str(jp.spread_mark(trade)))

# D54: stale chain mids must not mask a stop the price has already crossed —
# the exact WPM situation (frozen 3.6 mids at WPM 130.7 where intrinsic = 5.0)
jp._under_quote = lambda u: 130.7
QUOTES.update({f"WPM{_OCCD}P00140000": 2.3, f"WPM{_OCCD}P00135000": 0.3})
check("D54: stale mids floor at the intrinsic debit (5.0)",
      jp.spread_mark(trade) == 5.0, str(jp.spread_mark(trade)))
CLOSED.clear()
jp.manage_once(book(4.8))
check("D54: the floored mark fires the breached stop", bool(CLOSED), str(CLOSED))

print(("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
raise SystemExit(0 if not FAILS else 1)
