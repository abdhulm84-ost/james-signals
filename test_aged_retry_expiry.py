"""test_aged_retry_expiry.py — a QUEUED message keeps the date it was SENT (D69).

Monday's 0DTE signal ("for today") sat in the retry queue behind dead brain
credits. Parsed on Tuesday, `_signal_expiry` resolved "for today" against the
WALL CLOCK and produced Tuesday's expiry — a different, wrong trade.

Rule: expiry dates resolve from the message's ARRIVAL time (recorded as
`seen_at` in the retry record), never from the moment it finally parses.
Run: python test_aged_retry_expiry.py   (exit 0 = green)
"""
import time

import james_pipe as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


# freeze "now" on TUESDAY so the wall clock would give the wrong answer
mon = time.mktime(time.strptime("2026-11-16 16:00:00", "%Y-%m-%d %H:%M:%S"))
tue = time.mktime(time.strptime("2026-11-17 16:00:00", "%Y-%m-%d %H:%M:%S"))

rec_ok = {"seen_at": mon, "at": tue}
rec_legacy = {"at": mon}

asof = jp.us_session_at(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mon)))
res = jp._signal_expiry(None, "Sell SPX 7465P n Buy 7450P n collect $450 for today", asof=asof)
check("D69: a Monday 'for today' signal keeps MONDAY's session",
      res == "2026-11-16", str(res))

# the arrival stamp wins over the retry stamp
_arrival = (rec_ok.get("seen_at") or rec_ok.get("at") or 0)
asof2 = jp.us_session_at(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_arrival)))
check("D69: seen_at wins over the later retry stamp",
      jp._signal_expiry(None, "enter for today", asof=asof2) == "2026-11-16",
      str(jp._signal_expiry(None, "enter for today", asof=asof2)))

# legacy records (no seen_at) still anchor on their 'at'
_legacy = (rec_legacy.get("seen_at") or rec_legacy.get("at") or 0)
asof3 = jp.us_session_at(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_legacy)))
check("D69: legacy retry records anchor on 'at'",
      jp._signal_expiry(None, "enter for today", asof=asof3) == "2026-11-16",
      str(jp._signal_expiry(None, "enter for today", asof=asof3)))

# qty-1 default (user rule 2026-11-17): ambiguity always resolves to ONE lot
check("qty: ambiguity resolves to 1 lot",
      jp.resolve_qty(None, None, "") == 1, str(jp.resolve_qty(None, None, "")))
check("qty: an unparseable value still yields 1",
      jp.resolve_qty("abc") == 1, str(jp.resolve_qty("abc")))
# D72 (user rule 2026-11-17): qty 1 is a HARD CEILING until a band graduates.
# A stated size may never exceed it — that is the point of the cap.
check("qty: a stated size is CAPPED at 1 until graduation",
      jp.resolve_qty(4) == 1, str(jp.resolve_qty(4)))
check("qty: even a stated 10 resolves to 1",
      jp.resolve_qty(10) == 1, str(jp.resolve_qty(10)))

# --- D86: the SINGLE-LEG path must also anchor on arrival -----------------
# TL4399: "Sell QQQ 664P for today" sent Mon 02:19 UTC, parsed Tue 04:04.
# The single-leg path used the WALL CLOCK, so a Monday 0DTE became a Tuesday
# expiry — a different trade, 11 points deeper ITM than James ever was.
mon_ts = time.mktime(time.strptime("2026-11-16 02:19:00", "%Y-%m-%d %H:%M:%S"))
st_q = {"parse_retry": {"4399": {"at": mon_ts, "seen_at": mon_ts, "tries": 1}}}
asof_single = jp._asof_for_msg("4399", st_q)
check("D86: single-leg path anchors on arrival (Monday stays Monday)",
      asof_single is not None and asof_single.isoformat() == "2026-11-16",
      str(asof_single))
check("D86: the resolved expiry is the Monday session",
      jp._signal_expiry(None, "Sell QQQ 664P n collected $250 for today",
                        asof=asof_single) == "2026-11-16")
check("D86: a message with no queue record falls back to the wall clock",
      jp._asof_for_msg("nonexistent", {"parse_retry": {}}) is None)

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
