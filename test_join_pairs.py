"""test_join_pairs.py — peek-ahead join of split spread halves (DESIGN #2).

A 2-message spread ("Sell SPX 7520P" then "Buy SPX 7505P") must trade as ONE
structure, never as a naked leg. Both halves are already in the durable queue,
so this is a LOOK not a WAIT: peek at the next item and merge if it is the
complementary half. Genuine naked singles cost nothing - the next item simply
is not their partner.

A false merge (two real trades joined into one) would be worse than a miss, so
_joinable() requires: one leg each, opposite actions, same right, different
strikes, same underlying root.
Run: python test_join_pairs.py   (exit 0 = green)
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import james_pipe as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


# ---- 1. join detection -------------------------------------------------
cases = [
    ("Sell SPX 7520P", "Buy SPX 7505P", True, "bull put halves"),
    ("Sell SPX 7520C", "Buy SPX 7535C", True, "bear call halves"),
    ("Sell QQQ 664P", "Buy QQQ 661P", True, "ETF halves"),
    ("Sell SPX 7520P", "Sell SPX 7505P", False, "both sells"),
    ("Sell SPX 7520C", "Buy SPX 7505P", False, "different rights"),
    ("Sell SPX 7520P", "Buy QQQ 7505P", False, "different underlying"),
    ("Sell SPX 7520P", "Buy SPX 7520P", False, "same strike"),
    ("Sell SPX 7520P n Buy 7505P", "Buy SPX 7490P", False, "first is already complete"),
]
for a, b, exp, label in cases:
    got = jp._joinable(a, b)
    check(f"joinable: {label} -> {exp}", got == exp, f"got {got}")

# ---- 2. lone-leg detection --------------------------------------------
check("a single strike message is a lone leg",
      jp._is_lone_leg("Sell QQQ 664P n collected $250 for today"))
check("a complete spread is NOT a lone leg",
      not jp._is_lone_leg("Sell SPX 7520P n Buy 7505P n collect $615"))

# ---- 3. peek returns without removing ---------------------------------
tmp = pathlib.Path(tempfile.mkdtemp()) / "q.json"
q = jp.DurableQueue(tmp)
q.put("live", "Sell SPX 7520P", "910", prio=0)
q.put("live", "Buy SPX 7505P", "911", prio=0)
head = q.peek()
check("peek returns the next item", head and head["mid"] == "910", str(head))
check("peek does NOT remove it", q.depth() == 2, str(q.depth()))
nxt = q.peek(skip_mid="910")
check("peek(skip_mid) sees the partner", nxt and nxt["mid"] == "911", str(nxt))
check("the queue is still intact", q.depth() == 2, str(q.depth()))

# ---- 4. the merge leaves ONE item -------------------------------------
victim = q.peek(skip_mid="910")
q.drop(victim["mid"])
check("dropping the partner leaves one queued half", q.depth() == 1, str(q.depth()))

# ---- 5. suspect log exists as the evidence arm -------------------------
check("split_suspects ledger is defined",
      jp.SPLIT_SUSPECTS.name == "split_suspects.csv")

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
