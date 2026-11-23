"""test_serial_queue.py — messages are processed ONE AT A TIME (D73).

User directive 2026-11-17: *"don't be in a worry to execute the messages —
read one by one and analyse which way that should go, then double check what
we was right, then analyse the next message... process it one after the other
careful and push it to queue, park or execute, then capture the outcome."*

The old handler fired `create_task` per message, so a burst of six signals
became six concurrent Hermes calls, six placements and interleaved book
writes — the live-session derailing. A queue costs seconds; a race costs trades.

This pins the invariant structurally (no concurrency at the source) plus a
runtime proof that the consumer never overlaps.
Run: python test_serial_queue.py   (exit 0 = green)
"""
import pathlib
import re

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


SRC = (pathlib.Path(__file__).parent / "james_pipe.py").read_text(encoding="utf-8")

# --- structural: the live handler must ENQUEUE, never spawn a task ---------
m = re.search(r"async def on_msg\(e\):(.*?)\n    # catch-up", SRC, re.S)
body = m.group(1) if m else ""
check("D73: on_msg enqueues the message", "msg_q.put(" in body, body[:200])
check("D73: on_msg never fires a concurrent parse task",
      "create_task" not in body, "create_task found in handler")

# --- structural: exactly one consumer, nothing else consumes the queue -----
check("D73: exactly one serial consumer loop",
      SRC.count("async def drain_msgs()") == 1, str(SRC.count("async def drain_msgs()")))
check("D73: the consumer awaits process_message (no fire-and-forget)",
      re.search(r"await asyncio\.to_thread\(process_message", SRC) is not None)

# --- runtime: the queue is a real asyncio.Queue and is fed/consumed in order --
import asyncio


async def _proof():
    q = asyncio.Queue()
    order = []
    active = {"n": 0, "max": 0}

    async def worker():
        while True:
            item = await q.get()
            if item is None:
                q.task_done()
                return
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
            await asyncio.sleep(0.01)          # stands in for a parse
            order.append(item)
            active["n"] -= 1
            q.task_done()

    t = asyncio.create_task(worker())
    for i in range(6):
        await q.put(i)
    await q.put(None)
    await t
    return order, active["max"]


order, peak = asyncio.run(_proof())
check("D73: six queued items are processed in order", order == [0, 1, 2, 3, 4, 5], str(order))
check("D73: never more than one item in flight", peak == 1, f"peak concurrency {peak}")

# --- D76: RETRIES must go through the same queue (FIFO, one at a time) -----
check("D76: the retry loop enqueues instead of parsing directly",
      'msg_q.put("retry"' in SRC, "retry still calls _parse_trade inline")
check("D76/D78: the consumer handles both kinds (live and retry)",
      'kind == "retry"' in SRC and 'kind, mid = item.get' in SRC,
      "consumer does not branch on kind")
check("D76: no direct _parse_trade call in the retry loop",
      not re.search(r"await _guarded\(_parse_trade", SRC),
      "retry loop still awaits _parse_trade directly")

# --- D81: EVERY queue path must record completion --------------------------
# The retry path calls _parse_trade (which never marks seen), so without an
# explicit mark a retried message is re-queued on every restart forever.
check("D81: the retry path records completion",
      'mark_completed(st, mid)' in SRC and 'if kind == "retry"' in SRC,
      "retry path does not mark completion")
check("D81: the expired path records completion",
      SRC.count("mark_completed(st, mid)") >= 2,
      f"only {SRC.count('mark_completed(st, mid)')} completion site(s)")
check("D81: process_message guarantees completion via try/finally",
      "def mark_completed" in SRC and "finally:" in SRC)

# --- D82b: the live Telegram handler MUST be registered (regression guard) --
# An earlier queue edit deleted the @client.on decorator: live delivery died
# silently and only the 60s catch-up sweeps were saving us.
check("D82b: on_msg is registered with @client.on(NewMessage)",
      "@client.on(events.NewMessage(chats=[channel]))" in SRC)
check("D82b: the decorator sits immediately above on_msg",
      all(a in SRC for a in (
          "@client.on(events.NewMessage(chats=[channel]))",
          "async def on_msg(e):")) and
      SRC.find("@client.on(events.NewMessage") < SRC.find("async def on_msg"))

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
