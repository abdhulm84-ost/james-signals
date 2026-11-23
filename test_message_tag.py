"""test_message_tag.py — arrival tagging + LSE time/instrument gate (2026-11-18).

User design: tag every message with "what we can do" as it arrives, and add an
LSE flow check ONLY when it informs a decision:
  off-hours   -> LSE on every entry-shaped message
  market hours-> LSE on SPX entries only
  never       -> LSE on narrative/exit/link/empty

Advisory + archive only. LSE runs off-thread.
Run: python test_message_tag.py   (exit 0 = green)
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import message_tag as mt

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


# --- classifier ---------------------------------------------------------
check("entry: sell/buy/collect", mt.classify("Sell SPX 7520P n Buy 7505P n collect $615") == "entry")
# D112: past tense reads the same as present (D110's sibling bug)
check("entry: Sold/collected (past tense)",
      mt.classify("Sold QQQ 657P for today and collected $265") == "entry")
check("entry: Bought/paid (past tense)",
      mt.classify("Bought QQQ 657P n paid $265") == "entry")
check("exit: book/take gains", mt.classify("Pl book gains on all Bull Puts") == "exit")
check("hold: holding language", mt.classify("We are holding all bull puts") == "hold")
check("link: zoom/youtube", mt.classify("https://zoom.us/xyz") == "link")
check("empty: no text", mt.classify("") == "empty")
check("narrative: commentary", mt.classify("SPX is looking choppy") == "narrative")

# --- the LSE gate (the user's rule) ------------------------------------
check("off-hours entry SPX -> LSE", mt.should_lse("entry", "SPXW", "closed") is True)
check("off-hours entry WPM -> LSE (any underlying)", mt.should_lse("entry", "WPM", "closed") is True)
check("RTH entry SPX -> LSE", mt.should_lse("entry", "SPXW", "rth") is True)
check("RTH entry WPM -> NO LSE (SPX only)", mt.should_lse("entry", "WPM", "rth") is False)
check("RTH narrative -> NO LSE", mt.should_lse("narrative", "SPX", "rth") is False)
check("RTH exit -> NO LSE", mt.should_lse("exit", "SPX", "rth") is False)
check("off-hours exit -> NO LSE (never)", mt.should_lse("exit", "SPX", "closed") is False)
check("off-hours link -> NO LSE (never)", mt.should_lse("link", "SPX", "closed") is False)

# --- tag writes a row and returns the classification -------------------
row = mt.tag("TAG1", "Sell SPX 7520P n Buy 7505P n collect $615", "SPX",
             "closed", log=lambda m: None)
check("tag() classifies + suggests an action",
      row["classification"] == "entry" and "gates" in row["suggested_action"])

# --- upsert: arrival (no underlying) then post-parse (underlying) -------
import csv as _csv
mt.lse_flow = lambda u, lookback_days=1: (123456.0, 654321.0)   # hermetic stub
# D107: under a test module, tag() routes to message_tags.test.csv — the LIVE
# archive must never carry fixture ids (same leak class as D102's log spam).
_OUTP = mt._out_path()
mt.tag("UPS1", "Sell WPM 140P for earnings", "", "closed", log=lambda m: None)
mt.tag("UPS1", "Sell WPM 140P for earnings", "WPM", "closed", log=lambda m: None)
_ups = [r for r in _csv.DictReader(_OUTP.open(encoding="utf-8"))
        if r["msg_id"] == "UPS1"]
check("upsert: ONE row per message (not two)", len(_ups) == 1)
check("upsert: underlying filled post-parse", _ups and _ups[0]["underlying"] == "WPM")
check("D107: test run writes to test csv, not live", "test.csv" in str(_OUTP))

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
