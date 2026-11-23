"""test_log_isolation.py — test noise never reaches the production log (D102).

Test fixtures (order T1/T45, msg 2, msg X9) were writing into james_pipe.log,
which the watchdog + overnight watch scan - so every suite run inflated the
alert count ("10 issues", "145 signals") with fake problems. Under a test
module, log() must write to james_pipe.test.log instead.
Run: python test_log_isolation.py   (exit 0 = green)
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


SRC = pathlib.Path(jp.__file__).read_text(encoding="utf-8")

check("the test-run detector exists", "def _is_test_run()" in SRC)
check("log() routes by test context",
      'james_pipe.test.log' in SRC and 'james_pipe.log' in SRC)
check("a test argv is recognised", jp._is_test_run.__doc__ and "test_" in SRC)

# behavioural: under a test argv, log() must NOT touch james_pipe.log
prod = pathlib.Path(jp.__file__).parent / "james_pipe.log"
before = prod.stat().st_size if prod.exists() else 0
_orig_argv = sys.argv
sys.argv = ["test_log_isolation.py"]
jp.log("THIS IS TEST NOISE — must not reach the live log")
sys.argv = _orig_argv
after = prod.stat().st_size if prod.exists() else 0
check("test noise does not grow the production log", after == before,
      f"{before} -> {after}")

test_log = pathlib.Path(jp.__file__).parent / "james_pipe.test.log"
check("test noise lands in the throwaway log",
      test_log.exists() and "TEST NOISE" in test_log.read_text(encoding="utf-8", errors="replace"))

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
