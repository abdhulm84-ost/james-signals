"""test_pct_tighten.py — D121 bare-% = comment, explicit command still fires (2026-11-19).

The bug: msg 4496 '35% on bull puts' (a progress STATUS) closed the whole
position via the bare-% rule in non_entry_class + exit_command_wins. The real
instruction was the NEXT message (4497 'trim 80%'). We exited at -$40 instead
of holding to +$323.

The fix, with a hard constraint (user): tightening must NEVER drop a real
command. So:
  * bare '% on <family>' with NO action verb   -> narrative (lifecycle COMMENT)
  * explicit action verb (trim/cut/book/close/
cover/take/reduce/exit)    -> exit/trim, ALWAYS
  * conditional trade GUIDANCE (4501 'only if...') -> narrative
  * conditional REAL exit ('take gains only if green') -> exit still fires
Run: python test_pct_tighten.py   (exit 0 = green)
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


def both(text):
    return jp.non_entry_class(text), jp.exit_command_wins(text)


# --- 1. the bare % status (the 4496 bug) is now a comment ----------------
cls, ecw = both("35% on bull puts")
check("bare '35% on bull puts' -> narrative (not exit)",
      cls == "narrative" and ecw is False, f"cls={cls} ecw={ecw}")

cls, ecw = both("70% on SPX bear calls")
check("bare '70% on SPX bear calls' -> narrative", cls == "narrative" and ecw is False,
      f"cls={cls} ecw={ecw}")

# --- 2. EVERY explicit command form still fires (the "don't miss" clause) --
for cmd, want in (("trim 80%", "trim"), ("book gains", "exit"),
                  ("take gains", "exit"), ("cut 80%", "exit"),
                  ("cover 50%", "exit"), ("reduce 30%", "exit"),
                  ("Pl book gains on all Bull Puts", "exit"),
                  ("close it", "exit"), ("take 35% on bull puts", "exit")):
    cls, ecw = both(cmd)
    check(f"explicit command fires: '{cmd}'", cls == want and ecw is True,
          f"cls={cls} ecw={ecw}")

# --- 3. the real 4497 trim is unaffected --------------------------------
cls, ecw = both("40%, trim 80%")
check("4497 '40%, trim 80%' still a trim", cls == "trim" and ecw is True,
      f"cls={cls} ecw={ecw}")

# --- 4. conditional GUIDANCE (4501) is not an exit -----------------------
cls, ecw = both("Take Overnight trades only if you can close them overnight "
                "(or keep a limit order of 20%)")
check("4501 conditional guidance -> narrative (refused)",
      cls == "narrative" and ecw is False, f"cls={cls} ecw={ecw}")

# --- 5. a conditional REAL exit still fires (never miss) ------------------
cls, ecw = both("take gains only if it opens green")
check("conditional real exit 'take gains only if green' still fires",
      cls == "exit" and ecw is True, f"cls={cls} ecw={ecw}")

# --- 6. forecasts / narrative unaffected ---------------------------------
cls, ecw = both("SPX will make 100% tomorrow")
check("forecast stays prediction", cls == "prediction" and ecw is False,
      f"cls={cls} ecw={ecw}")
cls, ecw = both("SPX moved over 7410 and pulled back")
check("narrative stays narrative", cls == "narrative" and ecw is False,
      f"cls={cls} ecw={ecw}")

# --- 7. the safety net regex must not lose '%' at end-of-string ----------
import re
_jp_src = pathlib.Path(jp.__file__).read_text(encoding="utf-8", errors="replace")
check("safety net has no trailing \\b after the % branch",
      'r"\\bgains?\\b|\\bprofits?\\b|\\bout\\b|\\d{1,3}\\s*%"' in _jp_src or
      re.search(r're\.search\(r"\\bgains\?\\b.*\\d\{1,3\}\\s\*%"', _jp_src) is not None)

# --- D126: the arrival tag and the trade path must AGREE -----------------
# D121 put the guidance/bare-%/trim rules in james_pipe ONLY, so the arrival
# classifier (signal_intent) disagreed — msg 4501 tagged 'exit' on arrival and
# 'narrative' on the trade path. Both now share signal_intent's definitions.
_MGMT = {"exit", "trim"}
def _agree(a, b):
    if a in _MGMT and b in _MGMT: return True      # exit == trim
    if a == b: return True
    if a in ("entry", "unclear") and b not in _MGMT: return True
    if a == "narrative" and b == "prediction": return True
    return False

import signal_intent as _si
for _t in ("Take Overnight trades only if you can close them overnight "
           "(or keep a limit order of 20%)",
           "trim 80%", "40%, trim 80%", "35% on bull puts", "book gains",
           "cut 80%", "cover 50%", "SPX will make 100% tomorrow",
           "take gains only if it opens green",
           "SPX will cut losses if it breaks 7300"):
    _a, _b = _si.classify(_t), jp.non_entry_class(_t)
    check(f"D126: arrival==trade for {_t[:40]!r}", _agree(_a, _b),
          f"arrival={_a} trade={_b}")

# signal_intent must own ACTION_VERB (the SSOT home)
check("D126: signal_intent owns ACTION_VERB", hasattr(_si, "ACTION_VERB"))
check("D126: signal_intent owns is_guidance", callable(getattr(_si, "is_guidance", None)))

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
