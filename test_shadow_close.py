"""test_shadow_close.py — D120 shadow must settle on the official expiry close (2026-11-19).

The bug: manage_shadow settled SIM-alias entries at _under_quote() (a live CFD
print) instead of _expiry_close() (the official settlement). For SIM4495 — the
re-send of the 7325/7315 bull put — the live CFD read 7313.50 at the close
while the official print was 7323.63. A 10-point lag FLIPPED the verdict:
reported "-$560 (ack was right)" when the truth is "+$303 (MISSED TRADE)".

The fix: manage_shadow calls _expiry_close() first, falling back to
_under_quote() only when no official print exists — matching settle_expired.

This test checks the price-source ordering by static source inspection (the
shadow's own settle path), plus the verdict math at both prices.
Run: python test_shadow_close.py   (exit 0 = green)
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import james_pipe as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


# --- the verdict math: a 10-point settle difference flips the verdict ----
legs = [[7325.0, "put", "sell"], [7315.0, "put", "buy"]]
credit, qty = 4.4, 1.0
pnl_official = jp._spread_expiry_pnl(legs, credit, qty, 7323.63)
pnl_live = jp._spread_expiry_pnl(legs, credit, qty, 7313.50)
check("official close (7323.63) gives +$303 (missed trade)",
      round(pnl_official) == 303, str(pnl_official))
check("live lag (7313.50) gives -$560 (max loss)", round(pnl_live) == -560,
      str(pnl_live))
check("the two prices flip the verdict", (pnl_official > 0) != (pnl_live > 0))

# --- manage_shadow must prefer _expiry_close over _under_quote -----------
src = (HERE := pathlib.Path(jp.__file__)).read_text(encoding="utf-8", errors="replace")
ms = src[src.index("def manage_shadow"):src.index("def settle_expired")
         if "def settle_expired" in src else len(src)]
check("manage_shadow calls _expiry_close (official settlement)",
      "_expiry_close" in ms)
check("manage_shadow's official-close call precedes the _under_quote fallback",
      ms.find("_expiry_close") < ms.find("_under_quote"))

# --- the corrected record persists ---------------------------------------
st = json.loads(pathlib.Path(jp.__file__).parent.joinpath("state.json").read_text(encoding="utf-8"))
sh = (st.get("shadow") or {}).get("SIM4495")
check("SIM4495 settled on the official close (7323.63)",
      sh is not None and abs(float(sh.get("settle_px") or 0) - 7323.63) < 0.01,
      str(sh and sh.get("settle_px")))
check("SIM4495 verdict corrected to MISSED TRADE (+$303)",
      sh is not None and "MISSED" in str(sh.get("verdict", "")) and float(sh.get("pnl")) > 0,
      str(sh and sh.get("verdict")))

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
