"""test_band_segregation.py — the ticker class acts as the BAND (2026-11-18).

User: "he segregated the ticker... that segregation will act as each band".
A band is now keyed by instrument first, class second:
  SPX|vertical_credit|bullish|0DTE|fat|hold     <- tightest band
  STOCK|...  (nearest class match)               <- fallback for a fresh ticker
Canonicalisation means SPX/SPXW/SPX500 are ONE band, not three.

Advisory only: context, never a block, never a size.
Run: python test_band_segregation.py   (exit 0 = green)
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import james_patterns as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


# --- 1. the band carries the canonical instrument ----------------------
fp1 = jp.fingerprint("vertical_credit", "bullish", "0DTE", "fat", instrument="SPXW")
fp2 = jp.fingerprint("vertical_credit", "bullish", "0DTE", "fat", instrument="SPX500")
fp3 = jp.fingerprint("vertical_credit", "bullish", "0DTE", "fat", instrument="SPX")
check("SPXW and SPX500 land in the SAME band", fp1 == fp2 == fp3, f"{fp1} / {fp2} / {fp3}")

fpq = jp.fingerprint("vertical_credit", "bullish", "0DTE", "fat", instrument="QQQ")
check("QQQ is a DIFFERENT band from SPX", fpq != fp1, f"{fpq} vs {fp1}")

# --- 2. the instrument is the first field -----------------------------
check("the instrument leads the fingerprint", fp1.startswith("SPX|"), fp1)

# --- 3. two-level advisory -------------------------------------------
line, fp = jp.advisory("vertical_credit", "bullish", "0DTE", 7.0, 12.0,
                       instrument="SPXW")
check("a known instrument gets its own band", "SPX|" in line, line[:80])

line2, fp2b = jp.advisory("vertical_credit", "bearish", "0DTE", 4.5, 10.0,
                          instrument="MCHP")
check("an unseen ticker falls back to its CLASS band",
      ("closest match" in line2 or "no history in this class" in line2), line2[:100])
check("the fallback names the class and the real ticker",
      "STOCK" in line2 and "MCHP" in line2, line2[:110])

# --- 4. never blocks --------------------------------------------------
check("the advisory returns text, never raises",
      isinstance(line, str) and isinstance(line2, str))

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
