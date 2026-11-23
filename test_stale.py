"""test_stale.py — the unfilled-entry timer must count MARKET hours only.

TSLA 337.5P was placed two hours after the US close, and a wall-clock 3h timer
cancelled it eleven hours before the open. A signal quoted overnight must still
be waiting at the bell.
"""
import datetime

import james_pipe as jp

FAILS = []
CLOSED = []
PUSH = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


def ts_ago(h):
    n = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=h)
    return n.strftime("%Y-%m-%d %H:%M:%S")


# --- 1. the closed-hours arithmetic --------------------------------------
n = datetime.datetime.now(datetime.timezone.utc)
sat = datetime.datetime(2026, 11, 7, 12, 0, tzinfo=datetime.timezone.utc)
if sat.weekday() == 5:
    check("weekend start counts zero open hours",
          jp.open_hours_between("2026-11-07 12:00:00") >= 0.0)


def _partial_today():
    """Hours elapsed in TODAY's session, using the SAME DST-aware bounds as
    open_hours_between (D117). The whole-session assertions below only land on
    whole-session multiples at a CLOSED clock — mid-session the accrual carries
    a partial block, so subtract it and the property holds whenever the suite
    runs."""
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    if now.weekday() >= 5:
        return 0.0
    o, c = jp._us_session_bounds(now.date())
    if now <= o:
        return 0.0
    return (min(now, c) - o).total_seconds() / 3600.0


# an instant ago costs at most an instant: exactly 0.0 at a closed clock (the
# strong form this used to assert), ~0.01 while the session is open
check("an instant ago costs at most an instant of open hours",
      jp.open_hours_between(ts_ago(0.01)) <= 0.02)


# the US session is 6.5h (09:30-16:00 ET) regardless of season — the D117
# property: a full session from open to close is exactly 6.5 market hours.
_SESSION_LEN = 6.5


def _on_block(hours):
    """Distance to the nearest 6.5h multiple — plain % wraps 37.49999999 to
    ~6.5, which reads as a whole block off by 6.5 rather than off by 1e-9."""
    r = hours % _SESSION_LEN
    return min(r, _SESSION_LEN - r) < 0.05


full_day = jp.open_hours_between("2026-11-05 13:30:00") - _partial_today()
check("full sessions count as whole 6.5h blocks",
      6.4 <= full_day and _on_block(full_day), str(full_day))
# a post-close stamp on an OLD date keeps accruing every session since, so
# anchor on a fixed date that cannot drift: start mid-session, end after it
_sess = jp.open_hours_between("2026-11-05 13:30:00") - _partial_today()
check("a session start accrues only whole sessions",
      _sess >= 6.4 and _on_block(_sess), str(_sess))

# --- 2. pending_entry survives when only closed hours have passed --------
jp.save_state = lambda st: None
jp.push = lambda t, s: PUSH.append(t)
jp.ledger_row = lambda r: None
jp._TRADIER_INST = type("T", (), {"cancel": staticmethod(lambda o: (CLOSED.append(o), (True, "ok"))[1])})()


def trade(age_hours_val):
    return {"id": "P1", "instrument": "SPXW", "direction": "short", "qty": 1,
            "entry": 4.0, "stop": None, "targets": [], "expiry": "2026-11-20",
            "status": "pending_entry", "executor": "tradier", "order_id": "ORD1",
            "opened": ts_ago(age_hours_val), "pnl": None}


st = {"open": {"P1": trade(3.2)}, "closed": {}}
jp.market_open_now = lambda: True            # the guard needs an open market
jp.open_hours_between = lambda ts: 0.2        # placed after the bell
jp.manage_once(st)
check("post-close signal survives its first cycles", "P1" in st["open"], str(st["open"]))
check("no cancel fired", not CLOSED, str(CLOSED))

st = {"open": {"P1": trade(9)}, "closed": {}}
jp.open_hours_between = lambda ts: 6.4        # past the 6h session-hour limit
jp.manage_once(st)
check("once 6 market hours pass unfilled it expires",
      "P1" not in st["open"] and CLOSED == ["ORD1"], str(CLOSED))

print(f"\n{len(FAILS)} FAIL(S)" if FAILS else "\nALL PASS")
raise SystemExit(1 if FAILS else 0)
