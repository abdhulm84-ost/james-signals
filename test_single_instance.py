"""test_single_instance.py — D32: two pipes = duplicate listeners and book clobber.

The pid file was written but never checked, so a second james_pipe.py started
alongside the first. The guard now refuses when a live process owns the file.
Run: python test_single_instance.py   (exit 0 = green)
"""
import os
import tempfile

import james_pipe as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


d = tempfile.mkdtemp()
pf = os.path.join(d, "pipe.pid")

# use REAL live pids: a dead pid is (correctly) reclaimed
ME = os.getpid()                     # this process is alive
OTHER = 2 if os.name != "nt" else 4  # a Windows system process: live

check("first start writes the pid and claims no owner",
      jp.single_instance_guard(pf, pid=ME) is None, "should be None")
check("pid file holds our pid", open(pf).read().strip() == str(ME))

owner = jp.single_instance_guard(pf, pid=OTHER)
check("a live second instance is refused", owner == ME, str(owner))
check("the pid file is untouched by the refused start",
      open(pf).read().strip() == str(ME), open(pf).read().strip())

check("the same pid is not a conflict",
      jp.single_instance_guard(pf, pid=ME) is None, "should be None")

# a stale pid that no longer exists -> no owner, we claim it
os.remove(pf)
open(pf, "w").write("999999")           # not a live pid
claim = jp.single_instance_guard(pf, pid=ME)
check("a dead pid is reclaimed", claim is None, str(claim))
check("the reclaim wrote our pid", open(pf).read().strip() == str(ME),
      open(pf).read().strip())

print(("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
raise SystemExit(0 if not FAILS else 1)
