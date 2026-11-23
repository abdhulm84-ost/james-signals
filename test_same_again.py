"""test_same_again.py — D116 peek-back resolver (2026-11-19).

The miss: msg 4470 'enter the SAME bull puts again' carries no fresh
strike/premium — it points BACK at the 7335/7320 bull put we already held
(TL4439). The parser filed it as OPTION_IDEA and left +$1,000 on the table.

signal_intent.back_reference detects the back-reference; james_pipe's
resolve_same_structure resolves it STRICTLY: same canonical ticker + side +
right, 2-leg defined-risk only, a numeric level anchor breaks ties, ambiguity
never guesses.
Run: python test_same_again.py   (exit 0 = green)
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import signal_intent as si
import james_pipe as jp

FAILS = []
jp.push = lambda t, s: None


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


# --- detector -----------------------------------------------------------
check("4470 back-ref detected (bull puts again)",
      si.back_reference("we can enter same Bull Puts again") is not None)
check("fresh contract is NOT a back-ref",
      si.back_reference("Sell QQQ 665P n Buy 663P and collect around $116") is None)
check("hold language is NOT a back-ref",
      si.back_reference("We are holding all the bull puts") is None)

# --- resolver -----------------------------------------------------------
st = {
    "open": {},
    "closed": {
        "TL4439": {"id": "TL4439", "instrument": "SPXW", "direction": "short",
                   "exit_ts": "2026-11-17 18:33:25",
                   "legs": [[7335.0, "put", "sell"], [7320.0, "put", "buy"]]},
        "TL4461": {"id": "TL4461", "instrument": "SPXW", "direction": "short",
                   "exit_ts": "2026-11-18 14:45:26",
                   "legs": [[7360.0, "put", "sell"], [7345.0, "put", "buy"]]},
        "TL4433": {"id": "TL4433", "instrument": "SPXW", "direction": "short",
                   "legs": [[7340.0, "call", "sell"], [7355.0, "call", "buy"]]},
    },
}

text = ("After we entered bull Puts at 7320, SPX moved to 7415. If anyone missed "
        "entering yesterday, we can enter same Bull Puts again")
r = jp.resolve_same_structure(text, st, "4470")
check("4470 resolves to the 7335/7320 bull put (level anchor breaks tie)",
      r is not None and "7335" in r[1] and "7320" in r[1], str(r[1] if r else None))
check("resolved legs are sell 7335P / buy 7320P",
      r is not None and
      r[0]["spread"]["legs"] == [{"strike": 7335.0, "right": "put", "action": "sell"},
                                  {"strike": 7320.0, "right": "put", "action": "buy"}],
      str(r[0]["spread"]["legs"] if r else None))

# without the level anchor, two bull puts = ambiguous (never guess)
text_amb = "we can enter the same Bull Puts again"   # no '7320' level
check("no level anchor + two matches -> ambiguous (None)",
      jp.resolve_same_structure(text_amb, st, "4468") is None)

# a bear-call back-ref must not match the bull puts
check("bear calls back-ref does not match bull puts",
      jp.resolve_same_structure("enter the same Bear calls again", st, "4468") is None)

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
