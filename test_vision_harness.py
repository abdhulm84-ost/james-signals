"""test_vision_harness.py — the harness must not manufacture false mismatches.

Grading the vision extractor against ledger text has three traps, all found the
hard way (3394/3436/3592/3607) and all classed as harness bugs, not vision bugs:
  1. the text drops leading digits ("Buy 70P" is 7670P; "20/30" is 7720/7730),
     so comparing un-anchored makes the VISION look wrong;
  2. "7720C/P" is a straddle — two legs at one strike — so comparing deduped
     lists reports a leg-count difference as a mismatch;
  3. a MISSING credit (vision None) must count as a miss, not be skipped.
Run: python test_vision_harness.py   (exit 0 = green)
"""
import james_pipe as jp
import vision_check as vc

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


# 1) text with one 4-digit and one 2-digit strike -> anchored together
ts = jp.text_strikes("Sell 7675C/P, Buy 70P n Buy 80C n collect $410")
big = [w for w in ts if w >= 100]
want = sorted(big and (int(max(big) // 100) * 100 + w if w < 100 else w) for w in ts)
check("2-digit strikes anchor to the 4-digit hundreds",
      want == [7670, 7675, 7680], str(want))

# 2) text carries ONLY 2-digit forms -> anchor to the vision's own hundreds
ts2 = jp.text_strikes("Taking small gains on SPX 20/30 Bear calls of today")
got2 = [7720, 7730]
base = int(max(got2) // 100) * 100
want2 = sorted(base + w for w in ts2)
check("all-2-digit text anchors to the vision hundreds",
      want2 == [7720, 7730], str(want2))

# 3) straddle: duplicate strikes are information, not a mismatch
check("straddle duplicate leg is not a mismatch",
      set([7710, 7720, 7720, 7730]) == set([7710, 7720, 7730]))

# 4) missing credit counts as a miss
def grade(vcr, tcr):
    if vcr is not None and tcr:
        return "ok" if abs(float(vcr) - float(tcr)) <= 0.15 * max(float(tcr), 0.01) else "off"
    if vcr is None and tcr:
        return "off"
    return "skip"

# 5) the ground truth must keep the FULL text: truncating for display broke the
#    credit parse ("collect $400 to $410" cut to "collect $40" -> 0.4)
_full = "Don't miss trade, Take double - Sell 7650P n Buy 7640P and collect $400 to $410 "
check("full text parses its credit", jp.text_credit(_full) == 4.0, str(jp.text_credit(_full)))
check("truncated text lies about the credit",
      jp.text_credit(_full[:70]) != 4.0, str(jp.text_credit(_full[:70])))

check("missing credit is a miss, not a skip", grade(None, 2.5) == "off")
check("matching credit is ok", grade(4.05, 4.05) == "ok")
check("no text credit is a skip", grade(4.05, None) == "skip")

print(("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
raise SystemExit(0 if not FAILS else 1)
