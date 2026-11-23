"""test_preflight_boot.py — preflight must WAIT for MT5 at boot (D85).

At login, MT5, the hermes gateway and the pipe all start together. MT5 needs
20-60s. A first-look preflight used to report "no terminal/account info" and open
the session blind — the bug that once killed an entire session.

Fix: bounded wait (preflight_mt5_wait_s, default 120s), polling every 5s.
Run: python test_preflight_boot.py   (exit 0 = green)
"""
import inspect
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import james_pipe as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


SRC = inspect.getsource(jp.preflight)

# --- structural: the wait loop exists and is bounded ---------------------
check("D85: preflight has a bounded MT5 wait",
      "preflight_mt5_wait_s" in SRC and "_deadline" in SRC)
check("D85: it polls rather than failing on first look",
      SRC.count("terminal_info()") >= 1 and "time.sleep(5)" in SRC)
check("D85: the wait is capped (never hangs the boot)",
      re.search(r"time\.time\(\) >= _deadline", SRC) is not None)
check("D85: a genuine failure still reports clearly",
      "MT5 not ready after" in SRC)

# --- behavioural: a slow terminal is waited for, and success is reported --
class _SlowMT5:
    """terminal_info/account_info return None for the first N calls."""
    def __init__(self, ready_after):
        self.n = 0
        self.ready_after = ready_after

    def terminal_info(self):
        self.n += 1
        if self.n < self.ready_after:
            return None
        return type("TI", (), {"trade_allowed": True})()

    def account_info(self):
        if self.n < self.ready_after:
            return None
        return type("AI", (), {"login": 12345, "server": "Demo"})()


class _BBC:
    def __init__(self, mt):
        self._m = mt
        self.CONFIG = pathlib.Path("nonexistent.json")

    def connect(self):
        return True

    def _mt(self):
        return self._m

    def get_quote(self, inst):
        return 7500.0

    def resolve_symbol(self, inst):
        return inst


# stub the surrounding dependencies so preflight runs standalone
orig = (jp.bbc, jp.push, jp.log, jp._push_send, jp._TRADIER_INST)
jp.push = lambda t, s: None
jp.log = lambda m: None
jp._push_send = lambda t: None
jp.CFG = dict(jp.CFG)
jp.CFG["preflight_mt5_wait_s"] = 20
jp.CFG["dry_run"] = True

slow = _SlowMT5(ready_after=3)      # ready on the 3rd poll (~10s)
jp.bbc = lambda: _BBC(slow)
t0 = time.time()
try:
    jp.preflight({"open": {}, "closed": {}})
except Exception as e:
    print(f"   (preflight raised: {type(e).__name__}: {e})")
dt = time.time() - t0
check("D85: a terminal that arrives late is WAITED for (not failed)",
      slow.n >= 3, f"{slow.n} polls in {dt:.1f}s")
check("D85: the wait is bounded by the configured ceiling", dt <= 25, f"{dt:.1f}s")

# a terminal that never arrives must still return (bounded) and report
never = _SlowMT5(ready_after=10**9)
jp.bbc = lambda: _BBC(never)
t0 = time.time()
try:
    jp.preflight({"open": {}, "closed": {}})
except Exception:
    pass
dt2 = time.time() - t0
check("D85: a missing terminal times out instead of hanging",
      dt2 <= 30, f"{dt2:.1f}s")

(jp.bbc, jp.push, jp.log, jp._push_send, jp._TRADIER_INST) = orig
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
