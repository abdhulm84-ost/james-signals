"""test_d150_preopen.py — the pre-open gate must verify catch-up AND LSE.

D150: message catch-up and LSE callability were live-continuous/weekly but
absent from the pre-open audit — a deaf window or a dead key could hide in
logs while the gate said GO. Guards: both checks exist, are wired into
main()'s component loop, and the catch-up check reads the UTC gap-check
line (timegm, not mktime).

Run: python test_d150_preopen.py   (exit 0 = green)
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import system_audit as sa          # noqa: E402

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


src = (pathlib.Path(__file__).parent / "system_audit.py").read_text(
    encoding="utf-8")

check("D150: check_catchup exists and is callable",
      callable(getattr(sa, "check_catchup", None)))
check("D150: check_lse exists and is callable",
      callable(getattr(sa, "check_lse", None)))
i_main = src.index("def main()")
loop = src[i_main:i_main + 700]
check("D150: both checks wired into main's component loop",
      "check_catchup" in loop and "check_lse" in loop, loop[:200])
check("D150: catch-up parses the gap-check line as UTC (timegm, not mktime)",
      "timegm" in src[src.index("def check_catchup"):src.index("def check_lse")])
check("D150: LSE check does a live usage() call (key + quota end to end)",
      ".usage()" in src[src.index("def check_lse"):src.index("def _task_ready")])

# run both checks for real — they are read-only / 1 free call
import json
audit = {"max_qty": 1}
r1 = sa.check_catchup(audit)
check("D150: catch-up check passes on the live log", r1 is True)
r2 = sa.check_lse(audit)
check("D150: LSE check passes against the live key", r2 is True)

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
