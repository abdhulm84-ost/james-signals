"""test_durable_queue.py — the queue survives a crash (DESIGN #1, 2026-11-18).

Before this, the queue lived in memory and a message was marked `seen_msg` on
ENTRY. A restart mid-queue therefore lost every item still waiting, and the
lost ones looked already-handled forever — silent signal loss.

Now: disk is the source of truth (write on enqueue, remove on completion), and
`seen_msg` is written only when an item FINISHES.
Run: python test_durable_queue.py   (exit 0 = green)
"""
import json
import pathlib
import tempfile

import james_pipe as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


tmp = pathlib.Path(tempfile.mkdtemp()) / "q.json"

# ---- 1. enqueue persists to disk immediately ----------------------------
q = jp.DurableQueue(tmp)
check("put returns True for a new message",
      q.put("live", "Sell SPX 7520P", "100", prio=0) is True)
check("queue file exists on disk after put", tmp.exists())
on_disk = json.loads(tmp.read_text(encoding="utf-8"))
check("the item is on disk before any processing",
      len(on_disk) == 1 and on_disk[0]["mid"] == "100", str(on_disk))

# ---- 2. duplicate enqueue is refused ------------------------------------
check("re-enqueueing the same id is refused",
      q.put("live", "dup", "100", prio=0) is False)
check("queue depth unchanged after the duplicate", q.depth() == 1, str(q.depth()))

# ---- 3. CRASH SIMULATION: a new object reads the same file --------------
q2 = jp.DurableQueue(tmp)          # "process restarted"
check("a restart resumes queued work (nothing lost)",
      q2.depth() == 1 and q2.snapshot()[0]["mid"] == "100", str(q2.snapshot()))

# ---- 4. completion removes it from disk ---------------------------------
it = q2.pop()
check("pop returns the item", it and it["mid"] == "100", str(it))
check("pop removes it from disk (completed work is not re-run)",
      jp.DurableQueue(tmp).depth() == 0)

# ---- 5. PRIORITY: live outranks backlog, FIFO within a class ------------
q3 = jp.DurableQueue(tmp)
q3.put("live", "backlog-1", "201", prio=1)
q3.put("live", "stale-1", "202", prio=2)
q3.put("live", "live-1", "203", prio=0)
q3.put("live", "live-2", "204", prio=0)
order = [q3.pop()["mid"], q3.pop()["mid"], q3.pop()["mid"], q3.pop()["mid"]]
check("live messages run before backlog and stale",
      order == ["203", "204", "201", "202"], str(order))

# ---- 6. supersession: drop a queued id ---------------------------------
q4 = jp.DurableQueue(tmp)
q4.put("live", "entry", "300", prio=0)
q4.put("live", "other", "301", prio=0)
check("drop removes a specific queued message", q4.drop("300") is True)
check("the surviving message is untouched",
      [x["mid"] for x in q4.snapshot()] == ["301"], str(q4.snapshot()))
check("dropping an absent id is a no-op", q4.drop("999") is False)

# ---- 7. deadline field is carried ---------------------------------------
tmp5 = pathlib.Path(tempfile.mkdtemp()) / "q5.json"
q5 = jp.DurableQueue(tmp5)
q5.put("live", "0dte", "400", prio=0, deadline=1234567890)
check("a deadline survives enqueue", q5.snapshot()[0]["deadline"] == 1234567890)

tmp.unlink(missing_ok=True)
tmp5.unlink(missing_ok=True)
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
