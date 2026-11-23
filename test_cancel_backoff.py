"""test_cancel_backoff.py — a close that cannot cancel must not hammer forever.

TL3731 retried its cancel every ~65s for days; every listener hole found sits
inside one of those storms. Retries now back off (60s -> 15m) and stop at 20.
Run: python test_cancel_backoff.py   (exit 0 = green)
"""
import time
import datetime
import james_pipe as jp

FAILS = []
PUSH = []
# D40 (2026-11-21, clock family): pin the fixture expiry to the clock UNDER
# TEST — a hardcoded "2026-11-20" went stale the moment the US date rolled to
# 11-21: settle_expired settled X mid-test and the fixture KeyError'd. Every
# date-window fixture pins us_today (the same clock the code reads).
_EXPIRY = (jp.us_today() + datetime.timedelta(days=2)).isoformat()


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


jp.save_state = lambda st: None
jp.push = lambda t, s: PUSH.append(t)
jp.ledger_row = lambda r: None
jp.spread_mark = lambda t: None


class _T:
    def cancel(self, oid):
        return False, "HTTP 400 (PENDING)"


jp._TRADIER_INST = _T()
jp._tradier_close_status = lambda oid: ("PENDING", None)


def book(tries, last_at):
    return {"seen_msg": {"1": 1}, "closed": {}, "alerts_today": 0, "open": {"X": {
        "id": "X", "instrument": "SPXW", "symbol": "SPXW", "direction": "short",
        "qty": 1, "entry": 5.0, "expiry": _EXPIRY, "executor": "tradier",
        "status": "close_pending", "close_order_id": "O1", "cancel_close": True,
        "cancel_tries": tries, "cancel_at": last_at, "legs": [[7000.0, "put", "sell"]]}}}


# inside the backoff window: no attempt
t = book(mov := 6, time.time())
jp.manage_once(t)
check("recent attempt blocks a new try", t["open"]["X"]["cancel_tries"] == 6,
      str(t["open"]["X"].get("cancel_tries")))

# window elapsed: attempts again
t = book(6, time.time() - 999)
jp.manage_once(t)
check("elapsed window attempts the cancel", t["open"]["X"]["cancel_tries"] == 7,
      str(t["open"]["X"]["cancel_tries"]))

# 20th failure gives up
t = book(19, time.time() - 999)
PUSH.clear()
jp.manage_once(t)
x = t["open"]["X"]
check("20th try stops retrying", x.get("cancel_close") is False, str(x.get("cancel_close")))
check("stuck flag set", x.get("cancel_stuck") is True, str(x.get("cancel_stuck")))
check("alerted once", any("CANCEL STUCK" in p for p in PUSH), str(PUSH))

print(("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
raise SystemExit(0 if not FAILS else 1)
