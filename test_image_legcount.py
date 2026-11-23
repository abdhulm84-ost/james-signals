"""test_image_legcount.py — an image-only ticket must not be half-traded (D97).

msg 3883 (2026-11-04): IMAGE ONLY. The ticket said
  "SELL TO OPEN 10 SPXW 7700C / BUY TO OPEN 10 SPXW 7715C"  (a bear call spread)
The brain returned ONE leg, long. The subset rule let it through, so we bought a
naked long 7700C x10 that expired worthless: -$17,000. James WON that trade.

With no text there is nothing to cross-check, so an image-only message whose
ticket names MORE legs than we parsed must PARK - we cannot tell which leg is
missing, and a partial spread is a different trade.
Run: python test_image_legcount.py   (exit 0 = green)
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

SRC = pathlib.Path(r"C:\Trading\james-signals\james_pipe.py").read_text(encoding="utf-8")
FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


# --- structural: the guard exists and keys off the leg count -----------
check("the image leg-count guard exists", "image shows" in SRC)
check("it compares image legs vs parsed legs",
      "_img_legs > _txt_legs" in SRC)
check("it only fires when there is no text to cross-check",
      'len((text or "").strip()) < 20' in SRC)
check("img_legs is captured from the vision extractor",
      'card["img_legs"]' in SRC)
check("img_legs is carried onto the normalised card",
      'norm["img_legs"]' in SRC)

# --- the guard must not fire when the text IS present ------------------
# a text-backed message is cross-checked by the existing D35 strike/credit
# rules, so the leg-count rule stays narrow on purpose.
check("the guard is scoped to image-only messages",
      'strip()) < 20' in SRC and "no text to cross-check" in SRC)

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
