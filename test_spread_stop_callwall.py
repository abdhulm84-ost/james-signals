"""test_spread_stop_callwall.py — D82 credit-spread stop + D125 call-wall (2026-11-20).

D82: a credit spread with NO stated stop had NO exit protection — the manage
loop's stop block is guarded by `stop or targets`, so with both None it never
ran (TL4403 bled -670 instead of stopping out). synth_spread_stop() now
synthesises one at the single-leg convention (D87: single_stop_pct=40% ->
1.4x credit), CREDIT SPREADS ONLY — the manage loop's `mark >= stop` is a
loss test for a credit spread and would be a PROFIT test for a debit one.

D125: the fill re-gate's LSE flow check only ran for short PUTS, so a short
CALL spread (TL4500/TL4503) was gated on the ITM test alone while every short
put also got the tape check. The call wall (call premium at/above the strike)
is now symmetric.

Run: python test_spread_stop_callwall.py   (exit 0 = green)
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


# --- D82: stop synthesis ------------------------------------------------
check("credit put vertical gets a stop (1.4x credit)",
      jp.synth_spread_stop([(50.0, "put", "sell"), (45.0, "put", "buy")], 2.09) == 2.926,
      str(jp.synth_spread_stop([(50.0, "put", "sell"), (45.0, "put", "buy")], 2.09)))
check("credit call vertical gets a stop (TL4500)",
      jp.synth_spread_stop([(7365.0, "call", "sell"), (7385.0, "call", "buy")], 10.3) == 14.42,
      str(jp.synth_spread_stop([(7365.0, "call", "sell"), (7385.0, "call", "buy")], 10.3)))
check("DEBIT spread gets NO stop (manage's >= stop would fire on profit)",
      jp.synth_spread_stop([(7340.0, "call", "buy"), (7360.0, "call", "sell")], 11.6) is None)
check("no credit -> no stop (TL4503 before fill)",
      jp.synth_spread_stop([(7375.0, "call", "sell"), (7375.0, "call", "buy")], None) is None)
check("single leg -> no stop",
      jp.synth_spread_stop([(7360.0, "put", "sell")], 5.0) is None)
check("mixed rights -> no stop",
      jp.synth_spread_stop([(7360.0, "put", "sell"), (7380.0, "call", "buy")], 5.0) is None)
check("stop matches the single-leg convention (1.4x)",
      abs((jp.synth_spread_stop([(50.0, "put", "sell"), (45.0, "put", "buy")], 4.0) or 0)
          - 5.6) < 0.001)

# --- D125: the fill re-gate now covers CALLS as well as puts ------------
_src = pathlib.Path(jp.__file__).read_text(encoding="utf-8", errors="replace")
regate = _src[_src.index("def _fill_regate"):_src.index("def _message_tag_on_arrival")]
check("fill re-gate accepts both rights (p and c)",
      'if right in ("p", "c")' in regate)
check("fill re-gate picks the contract type from the right",
      '_ctype = "put" if right == "p" else "call"' in regate)
check("call wall filters at/above the strike",
      "float(r[\"strike\"]) >= strike" in regate)
check("put wall still filters at/below the strike",
      "float(r[\"strike\"]) <= strike" in regate)

# --- the live positions now carry stops ---------------------------------
# D128 (2026-11-20): TL4415/TL4455/TL4500 closed during the 11-19 session —
# stops are verified in whichever book still holds the trade.
import json
st = json.loads(pathlib.Path(jp.__file__).parent.joinpath("state.json").read_text(encoding="utf-8"))
for tid in ("TL4415", "TL4455", "TL4500"):
    v = (st.get("open") or {}).get(tid) or (st.get("closed") or {}).get(tid)
    check(f"{tid} carries a synthesised stop", v is not None and v.get("stop"),
          str(v and v.get("stop")))

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
