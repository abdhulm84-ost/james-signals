"""test_colour_arm_instrument.py — D124 a colour level arms only its own instrument (2026-11-20).

The bug: msg 4502 '7345 target' is an SPX level, but `_structure_family`
returns None for it (no 'puts'/'calls' word), so the family guard was skipped
and the level armed as an exit target on SOXL (TL4415) and NVDA (TL4455).
Those underlyings trade ~50 and ~155 — a 7345 target can never be reached, so
the armed target was dead and would mask a real one.

The fix: arm_colour_levels takes the level's instrument and only arms
positions in that instrument (canonicalised: SPXW/SPX both -> SPX; SOXL/NVDA
do not match). Unknown instruments on either side stay lenient.
Run: python test_colour_arm_instrument.py   (exit 0 = green)
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


def book():
    return {"open": {
        "TL4415": {"id": "TL4415", "instrument": "SOXL", "status": "open_tradier",
                   "legs": [[50.0, "put", "sell"], [45.0, "put", "buy"]]},
        "TL4455": {"id": "TL4455", "instrument": "NVDA", "status": "open_tradier",
                   "legs": [[155.0, "put", "sell"], [150.0, "put", "buy"]]},
        "TLX": {"id": "TLX", "instrument": "SPXW", "status": "open_tradier",
                "legs": [[7360.0, "put", "sell"], [7345.0, "put", "buy"]]},
    }}


jp.push = lambda t, s: None
jp.ledger_row = lambda r: None

# --- the 4502 case: an SPX level must not arm SOXL/NVDA ------------------
st = book()
armed = jp.arm_colour_levels([(7345.0, "target")], "7345 target", "4502", st,
                              instrument="SPX500")
check("SPX level arms ONLY the SPX position", armed == ["TLX"], str(armed))
check("SOXL not armed by an SPX level",
      not st["open"]["TL4415"].get("exit_target"))
check("NVDA not armed by an SPX level",
      not st["open"]["TL4455"].get("exit_target"))
check("SPXW position IS armed", st["open"]["TLX"].get("exit_target") is not None)

# --- SPXW (position) matches an SPX500 (level) via canonicalisation ------
st = book()
for k in list(st["open"]):
    if k != "TLX":
        del st["open"][k]
armed = jp.arm_colour_levels([(7345.0, "target")], "7345 target", "4502", st,
                             instrument="SPX")
check("SPX level + SPXW position match (canonical)", armed == ["TLX"], str(armed))

# --- unknown instruments stay lenient (no false block) -------------------
st = book()
for k in list(st["open"]):
    if k != "TL4415":
        del st["open"][k]
armed = jp.arm_colour_levels([(7345.0, "target")], "7345 target", "4502", st,
                             instrument=None)
check("unknown level instrument is lenient (arms)", armed == ["TL4415"], str(armed))

# --- a family still filters (unchanged behaviour) ------------------------
# instrument=None so ONLY the family guard applies: both put spreads pass.
st = book()
armed = jp.arm_colour_levels([(7345.0, "target")], "7345 bull puts target", "4503",
                              st, instrument=None)
check("family word still filters to puts", "TL4415" in armed and "TLX" in armed,
      str(armed))

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
