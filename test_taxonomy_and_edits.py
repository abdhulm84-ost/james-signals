"""test_taxonomy_and_edits.py — canonical tickers + edit tracking (D93/D94).

Two gaps found by comparing our pipeline with a friend's journal build:

1. TAXONOMY: our ledger carried 110 raw ticker strings for far fewer real
   instruments - SPX arrived as SPX / SPXW / SPX500 / S&P500 / S&P, plus 190
   OCC contract codes stored as "symbols" and prose noise ("MK"). Fragmented
   keys split the pattern table and confuse the flow lookup.

2. EDITS: his journal shows 52 revisions for 25 messages (over 2x). We treated
   an edit as a duplicate or nothing, so a CORRECTED credit or strike was never
   seen - we could trade a value James had already fixed.

Run: python test_taxonomy_and_edits.py   (exit 0 = green)
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import james_pipe as jp
import ticker_taxonomy as tx

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


# --- 1. taxonomy: one name per instrument -------------------------------
for raw, canon, bucket in [
        ("SPXW", "SPX", "index"), ("SPX500", "SPX", "index"),
        ("SPX", "SPX", "index"), ("S&P500", "SPX", "index"),
        ("RUTW", "RUT", "index"), ("NDX100", "NDX", "index"),
        ("SPY", "SPY", "etf"), ("QQQ", "QQQ", "etf"), ("SOXL", "SOXL", "etf"),
        ("WPM", "WPM", "stock"), ("TSLA", "TSLA", "stock")]:
    c, b = tx.normalise(raw)
    check(f"normalise {raw} -> {canon}/{bucket}", (c, b) == (canon, bucket), f"{c}/{b}")

# --- 2. taxonomy: junk is rejected, never becomes an instrument --------
for raw, bucket in [("BLK261106P01185000", "contract"),
                    ("SPXW261105C07700000", "contract"),
                    ("MK", "junk"), ("", "junk")]:
    c, b = tx.normalise(raw)
    check(f"{raw!r} is not an instrument ({bucket})", c is None and b == bucket, f"{c}/{b}")

check("_canon_underlying rejects an OCC code",
      jp._canon_underlying("BLK261106P01185000") is None)
check("_canon_underlying rejects the MK signature",
      jp._canon_underlying("MK") is None)
check("_canon_underlying still canonicalises SPXW",
      jp._canon_underlying("SPXW") == "SPX")

# --- 3. edits: a changed level is DETECTED -----------------------------
base = "Sell SPX 7520P n Buy 7505P n collect $615"
check("a changed CREDIT is detected",
      jp._levels_changed(base, "Sell SPX 7520P n Buy 7505P n collect $600"))
check("a changed STRIKE is detected",
      jp._levels_changed(base, "Sell SPX 7535P n Buy 7505P n collect $615"))
check("a flipped SIDE is detected",
      jp._levels_changed("Sell SPX 7520P", "Buy SPX 7520P"))

# --- 4. edits: noise is NOT an alarm -----------------------------------
check("punctuation-only edit does not alarm",
      not jp._levels_changed("Sell SPX 7520P n Buy 7505P",
                             "Sell SPX 7520P n Buy 7505P."))
check("an identical revision does not alarm",
      not jp._levels_changed(base, base))
check("a first revision (no previous text) does not alarm",
      not jp._levels_changed("", base))

# --- 5. the edit handler is registered --------------------------------
SRC = pathlib.Path(jp.__file__).read_text(encoding="utf-8")
check("the MessageEdited handler is registered",
      "@client.on(events.MessageEdited" in SRC)
check("revisions are recorded in the ledger", '"event": "EDIT"' in SRC)

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
