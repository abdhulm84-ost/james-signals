"""test_open_hours_dst.py — D117 US session bounds must be DST-aware (2026-11-19).

The D40 clock-family, sixth member: `open_hours_between` hardcoded the US open
as 13:30 UTC and close as 21:00 UTC — the SUMMER (EDT) offset. In winter (EST,
the current season) 13:30 UTC is 08:30 ET, a full hour BEFORE the real 09:30 ET
open. A pre-market signal (08:30-09:30 ET) was wrongly counted as in-hours, so
its retry timer burned during pre-market and the entry cancelled ~1h early.

The fix derives open/close from the DST-aware America/New_York zone, so the
boundary is always correct regardless of season.
Run: python test_open_hours_dst.py   (exit 0 = green)
"""
import datetime
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import james_pipe as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


# winter (EST, UTC-5): open 09:30 ET = 14:30 UTC, close 16:00 ET = 21:00 UTC
w = jp._us_session_bounds(datetime.date(2026, 11, 18))
check("winter open = 14:30 UTC",
      w[0].hour == 14 and w[0].minute == 30, str(w[0]))
check("winter close = 21:00 UTC",
      w[1].hour == 21 and w[1].minute == 0, str(w[1]))

# summer (EDT, UTC-4): open 09:30 ET = 13:30 UTC, close 16:00 ET = 20:00 UTC
s = jp._us_session_bounds(datetime.date(2026, 7, 15))
check("summer open = 13:30 UTC",
      s[0].hour == 13 and s[0].minute == 30, str(s[0]))
check("summer close = 20:00 UTC",
      s[1].hour == 20 and s[1].minute == 0, str(s[1]))

# the regression that matters: a pre-market winter signal must NOT accumulate
# market hours from 08:30 ET. 13:36 UTC is 08:36 ET (pre-market, EST). From
# that moment to the 09:30 ET open (14:30 UTC), zero market hours pass.
check("winter pre-market signal accumulates no phantom hour (08:36 ET start)",
      jp._us_session_bounds(datetime.date(2026, 11, 18))[0].hour == 14,
      "open is 14:30 UTC in winter, not 13:30")

# a signal placed AT the winter open (14:30 UTC) is the first in-hours moment
o = jp._us_session_bounds(datetime.date(2026, 11, 18))[0]
check("winter open is one hour later than the old hardcode (13:30 -> 14:30)",
      o.hour == 14, f"open {o}")

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
