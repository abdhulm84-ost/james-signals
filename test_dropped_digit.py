"""test_dropped_digit.py — D109 dropped-leading-digit repair (2026-11-19).

The original miss: msg 4459 'SPX Sell 7360P n Buy 345P n collect $810 to $815
for tomorrow' parked because 345 sat outside 0.5-1.5x the structure median
7360. James's own portfolio snapshot (msg 4464, same session) showed he held
"SPXW 7360 / 7345 Puts" — the thousands digit had fallen off '7345P'. The
trade won +$1,000 to +$1,320. We parked it.

Rule: on an INDEX root (SPX/RUT/NDX), a 2-leg spread with one index-scale leg
(>=1000) and one 100-999 leg is a typo — no index option carries a 345 strike.
Pad the small leg with the big leg's leading digits; adopt only when the
candidate lands within 100 pts of the big leg.
Run: python test_dropped_digit.py   (exit 0 = green)
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import james_pipe as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


def sig(legs):
    return [(l["action"], l["strike"], l["right"]) for l in legs]


# --- the 4459 repair ----------------------------------------------------
legs = [{"action": "sell", "strike": 7360.0, "right": "put"},
        {"action": "buy", "strike": 345.0, "right": "put"}]
out = jp.repair_dropped_digit(legs, "SPXW")
check("4459: 345P -> 7345P beside 7360P",
      out is not None and sig(out) == [("sell", 7360.0, "put"), ("buy", 7345.0, "put")],
      str(sig(out)) if out else "None")

# --- negatives ----------------------------------------------------------
eq = [{"action": "sell", "strike": 355.0, "right": "call"},
      {"action": "buy", "strike": 345.0, "right": "call"}]
check("equity scale (355/345) NOT repaired",
      jp.repair_dropped_digit(eq, "WPM") is None)

ok = [{"action": "sell", "strike": 7360.0, "right": "put"},
      {"action": "buy", "strike": 7345.0, "right": "put"}]
check("normal index spread (7360/7345) untouched",
      jp.repair_dropped_digit(ok, "SPXW") is None)

# two index-scale legs, no dropped digit
both = [{"action": "sell", "strike": 7360.0, "right": "put"},
        {"action": "buy", "strike": 7000.0, "right": "put"}]
check("two index-scale legs NOT repaired (no 3-digit leg)",
      jp.repair_dropped_digit(both, "SPXW") is None)

# dropped digit but the candidate lands too far (would be a genuinely wrong
# pad — e.g. 1360/345 would pad to 1345, 15 wide, fine; but 1000/345 -> 345
# pads to 345? no: 1000 is 4 digits, 345 is 3, pad '1' -> 1345, width -345).
far = [{"action": "sell", "strike": 1000.0, "right": "put"},
       {"action": "buy", "strike": 345.0, "right": "put"}]
check("candidate outside 0..100 of the big leg NOT adopted",
      jp.repair_dropped_digit(far, "SPXW") is None)

# single leg -> not a spread, no repair
single = [{"action": "sell", "strike": 345.0, "right": "put"}]
check("single leg NOT repaired",
      jp.repair_dropped_digit(single, "SPXW") is None)

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
