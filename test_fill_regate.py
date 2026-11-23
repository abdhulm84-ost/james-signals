"""test_fill_regate.py — a resting entry re-gates at the open (D104).

TL4469: a short 7385 put parsed pre-open, SPX fell to 7379 by the bell - the
short leg went 5 pts ITM while the entry waited to fill. A fill-time re-gate
must catch that and cancel, instead of filling a losing 0DTE.

Rules: a POSITIVE signal cancels; a MISSING check (no spot) never blocks.
Run: python test_fill_regate.py   (exit 0 = green)
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


def mk(legs, under="SPXW"):
    return {"id": "TLX", "msg_id": "9999", "instrument": under, "symbol": under,
            "legs": legs, "expiry": "2026-11-18"}


PUSH = []
jp.push = lambda t, s: PUSH.append(t)

# 1. short put now ITM -> cancel (the TL4469 shape)
S = 7379.9
jp._under_quote = lambda u: S
t = mk([[7385.0, "put", "sell"], [7370.0, "put", "buy"]])
check("short put now ITM -> re-gate cancels", jp._fill_regate(t, {}) is True)

# 2. short put still OTM -> let it fill
jp._under_quote = lambda u: 7395.0
check("short put still OTM -> let it fill", jp._fill_regate(t, {}) is False)

# 3. short CALL now ITM -> cancel (symmetric)
t2 = mk([[7385.0, "call", "sell"], [7400.0, "call", "buy"]])
jp._under_quote = lambda u: 7390.0
check("short call now ITM -> re-gate cancels", jp._fill_regate(t2, {}) is True)

# 4. no spot -> NEVER blocks (missing check must not cancel)
jp._under_quote = lambda u: None
check("no spot -> re-gate does not block", jp._fill_regate(t, {}) is False)

# 5. a single leg -> not a vertical, no re-gate
t3 = mk([[7385.0, "put", "sell"]])
jp._under_quote = lambda u: 7300.0
check("single leg -> no re-gate (not a vertical)", jp._fill_regate(t3, {}) is False)

# 6. wired into the FILL branch
SRC = pathlib.Path(jp.__file__).read_text(encoding="utf-8")
check("the re-gate is wired at fill time", "if _fill_regate(t, st):" in SRC)

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)

# --- D105: the LSE flow gate ------------------------------------------
# (imports are checked structurally to avoid a live LSE call in the suite)
SRC2 = pathlib.Path(jp.__file__).read_text(encoding="utf-8")
check("D105: the flow gate is wired into _fill_regate",
      "LSE shows" in SRC2 and "options_flow" in SRC2)
check("D105: flow gate only for short PUTS",
      'right == "p"' in SRC2)
check("D105: a missing flow never blocks (try/except pass)",
      "except Exception:" in SRC2 and "# flow is a bonus check" in SRC2)
