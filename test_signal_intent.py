"""test_signal_intent.py — D113 single-source verb lexicon + unclear→LSE + prior-shape (2026-11-19).

The meta-bug behind D110/D111/D112: the verb knowledge lived in THREE places
(text_credit, text_is_naked_short, message_tag.classify) and each fix patched
only one — so 'Sold X collected $Y' kept resurfacing. signal_intent.py is the
ONE lexicon every consumer imports. It also encodes two user rules:
  1. an unclear message (strike+ticker/premium, no clean verb) is 'unclear',
     NOT 'narrative' — and 'unclear' still triggers the LSE flow check.
  2. prior_shape.lookup() finds the same structural shape in the defect
     register / ledger / park outcomes and surfaces what happened before.
Run: python test_signal_intent.py   (exit 0 = green)
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import signal_intent as si
import message_tag as mt
import prior_shape as ps

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


# --- the ONE lexicon ----------------------------------------------------
check("sold+collected -> short",
      si.direction("Sold QQQ 657P for today and collected $265") == "short")
check("sell+collect -> short",
      si.direction("Sell QQQ 657P for today and collect $265") == "short")
check("buy -> long", si.direction("Buy QQQ 657P for today") == "long")
check("bought+paid -> long",
      si.direction("Bought QQQ 657P n paid $265") == "long")
check("paired sell+buy (spread) -> None",
      si.direction("Sell SPX 7600P n Buy 7590P n collect $475") is None)

# --- unclear vs narrative (the user rule) -------------------------------
check("'Sold X collected Y' classifies ENTRY (not narrative)",
      si.classify("Sold QQQ 657P for today and collected $265") == "entry")
check("narrative stays narrative",
      si.classify("SPX is looking choppy") == "narrative")
check("recap 'took gains' stays narrative",
      si.classify("We took gains on 2 SPX Bull Puts premarket") == "narrative")

# a strike+ticker with no clean verb is UNclear, not narrative
check("strike+ticker, no verb -> unclear",
      si.classify("QQQ 657P for today $265") == "unclear")

# --- unclear triggers LSE (the second user rule) ------------------------
check("unclear off-hours -> LSE (not skipped)",
      mt.should_lse("unclear", "QQQ", "closed") is True)
check("unclear RTH SPX -> LSE",
      mt.should_lse("unclear", "SPXW", "rth") is True)
check("narrative off-hours -> NO LSE (still skipped)",
      mt.should_lse("narrative", "QQQ", "closed") is False)

# --- prior-shape lookup -------------------------------------------------
r = ps.lookup("Sold QQQ 657P for today and collected $265")
check("prior-shape flags the D110/D112 verb defects",
      any(d["id"] in ("D110", "D112") for d in r["defects"]),
      str([d["id"] for d in r["defects"]]))
check("prior-shape carries a prior outcome for QQQ",
      r["prior"] is not None and r["prior"]["instrument"] == "QQQ")

# --- james_pipe delegates to signal_intent ------------------------------
import james_pipe as jp
check("text_is_naked_short delegates (sold+collected -> True)",
      jp.text_is_naked_short("Sold QQQ 657P for today and collected $265") is True)
check("text_is_naked_short spread stays False",
      jp.text_is_naked_short("Sell SPX 7600P n Buy 7590P n collect $475") is False)

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
