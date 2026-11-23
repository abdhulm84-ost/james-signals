"""test_levels_view.py — a futures ladder + a view line (msg 3657).

S1..R3 with dollar signs and 'bearish below X / bullish above Y' used to yield
one level and no plan. Both are context, captured not traded.
Run: python test_levels_view.py   (exit 0 = green)
"""
import james_pipe as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


T = """S1:$28620
S2:$28530
R1:$28960
View: bearish below 28710
bullish above $28880"""

lv = dict((r, k) for k, r in jp.text_levels(T))
check("S1 ladder captured", lv.get("S1") == 28620.0, str(lv))
check("R1 ladder captured", lv.get("R1") == 28960.0)
check("below view captured", lv.get("below") == 28710.0)
trg = jp.text_trigger(T) or ""
check("the two-sided view is kept as a plan",
      "bearish below" in trg.lower() and "bullish above" in trg.lower(), trg)

print(f"\n{len(FAILS)} FAIL(S)" if FAILS else "\nALL PASS")
raise SystemExit(1 if FAILS else 0)
