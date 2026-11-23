"""test_rights_inherit.py — "Read this as ..." corrections carry no rights.

Msg 3903 ("Read this as Sell 7705 n buy 7715 for today") has legs but no C/P,
and with no card legs it parked as "no parseable legs". The right KIND is
recoverable from the parked structure that preceded it — the correction shifts
strikes by a few points, so proximity decides, not equality.
Run: python test_rights_inherit.py   (exit 0 = green)
"""
import calendar
import time
import james_pipe as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


jp.log = lambda m: None
jp.ledger_row = lambda r: None


def at(stamp):
    return calendar.timegm(time.strptime(stamp, "%Y-%m-%d %H:%M:%S"))


def legs():
    return [{"action": "sell", "strike": 7705.0, "right": None},
            {"action": "buy", "strike": 7715.0, "right": None}]


got = jp.inherit_rights_from_park(legs(), "Read this as Sell 7705 n buy 7715",
                                  at("2026-11-04 18:20:00"))
check("right kind is recovered from the prior park",
      got and all(l["right"] == "call" for l in got), str(got))
check("strikes stay the correction's own",
      got and [l["strike"] for l in got] == [7705.0, 7715.0], str(got))

far = jp.inherit_rights_from_park(
    [{"action": "sell", "strike": 5000.0, "right": None},
     {"action": "buy", "strike": 5010.0, "right": None}],
    "Read this as Sell 5000 n buy 5010", at("2026-11-04 18:20:00"))
check("nothing near enough -> None", far is None, str(far))

old = jp.inherit_rights_from_park(legs(), "Read this as", at("2026-12-01 12:00:00"))
check("outside the 24h window -> None", old is None, str(old))

print(("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
raise SystemExit(0 if not FAILS else 1)
