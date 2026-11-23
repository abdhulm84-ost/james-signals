"""test_gap_backfill.py — the 10-minute reconciler must actually RUN.

2026-11-02: msgs 3734 (a real SPX bull put) and 3735 were dropped by the
listener. gap_check existed but went through asyncio.to_thread on an async
function, so the coroutine was never awaited and the check silently did
nothing for its whole life. Nothing was ever logged by it.
Run: python test_gap_backfill.py   (exit 0 = green)
"""
import asyncio
import james_pipe as jp

FAILS = []
SEEN = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


jp.log = lambda m: None


class _M:
    def __init__(self, i, text="x", photo=False):
        self.id, self.message, self.photo = i, text, photo
        self.date = None

    async def download_media(self, file):
        self.downloaded = file


class _Client:
    def __init__(self, msgs):
        self.msgs = msgs

    def iter_messages(self, channel, limit=50):
        async def gen():
            for m in self.msgs:
                yield m
        return gen()


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


st = {"seen_msg": {"100": 1, "101": 1, "103": 1}}
got = run(jp.gap_backfill(_Client([_M(99), _M(100), _M(101), _M(102), _M(103)]),
                          "chan", st, lambda t, i, d, p, s, stale=False: SEEN.append(i)))
check("the hole 102 is recovered", 102 in (got or []), str(got))
check("unseen id below the last is also recovered (out-of-order)", 99 in (got or []), str(got))
check("seen ids are not re-run", 100 not in (got or []) and 101 not in (got or []), str(got))
SEEN.clear()
st2 = {"seen_msg": {"100": 1, "101": 1, "102": 1}}
got2 = run(jp.gap_backfill(_Client([_M(100), _M(101), _M(102)]), "chan", st2,
                           lambda t, i, d, p, s, stale=False: SEEN.append(i)))
check("a closed gap does nothing", got2 == [] and not SEEN, str(got2))

# an empty book never runs (no last id to anchor from)
got3 = run(jp.gap_backfill(_Client([_M(5)]), "chan", {"seen_msg": {}},
                           lambda *a: SEEN.append(1)))
check("no anchor -> no-op", got3 == [], str(got3))

# the wrapper must classify the async fn as a coroutine function
check("gap_backfill is recognised as async", asyncio.iscoroutinefunction(jp.gap_backfill))

print(("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
raise SystemExit(0 if not FAILS else 1)
