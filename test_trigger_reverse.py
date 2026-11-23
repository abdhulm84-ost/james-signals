"""test_trigger_reverse.py — condition-first conditional plans (msg 3419).

'If Price goes to the top, we can enter Bear calls again' is a documented plan:
condition first, command after. Only the forward order (command ... if ...) was
matched, so these fell through to INVALID with nothing captured.
Run: python test_trigger_reverse.py   (exit 0 = green)
"""
import james_pipe as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


check("reversed conditional captured",
      jp.text_trigger("If Price goes to the top, we can enter Bear calls again")
      is not None)
check("forward conditional still works",
      jp.text_trigger("we can enter again if it breaks the box") is not None)
check("'will wait for pull back to enter' still works",
      jp.text_trigger("we will wait for pull back to enter bear calls") is not None)
check("plain narrative is not a trigger",
      jp.text_trigger("SPX looking choppy today") is None)
check("a plain entry is not a trigger",
      jp.text_trigger("Sell SPX 7640P n Buy 7630P n collect $470") is None)

print(("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
raise SystemExit(0 if not FAILS else 1)
