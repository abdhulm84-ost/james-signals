"""test_trade_lifecycle.py — D119 per-trade narrative tracker (2026-11-19).

The user's proposal: when a narrative arrives that describes a trade direction,
open a lifecycle ID. As subsequent messages reference that trade (trim, add,
exit, commentary), append to the same ID. When the narrative closes, mark the
outcome. Each trade becomes a readable story instead of scattered ledger rows.

trade_lifecycle.py is advisory + archive only: it never trades, never blocks.
It writes to trade_lifecycle.csv (append-only) and provides a report() reader.
Run: python test_trade_lifecycle.py   (exit 0 = green)
"""
import csv
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import trade_lifecycle as tlc

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


# isolate the test from the live archive
_TMP = tempfile.TemporaryDirectory()
tlc.OUT = pathlib.Path(_TMP.name) / "trade_lifecycle.csv"

# --- open a lifecycle ---------------------------------------------------
tlc.open_lifecycle("TL4494", "4494", "SPXW", "sell 7325P/buy 7315P @ 4.60")
events = tlc.get_lifecycle("TL4494")
check("OPEN event recorded", len(events) == 1 and events[0]["event"] == "OPEN")
check("lifecycle_id matches trade_id", events[0]["lifecycle_id"] == "TL4494")

# --- add (re-send) ------------------------------------------------------
tlc.add_to_lifecycle("TL4494", "4495", "re-send (image-only)")
events = tlc.get_lifecycle("TL4494")
check("ADD event appended", len(events) == 2 and events[1]["event"] == "ADD")

# --- trim ---------------------------------------------------------------
tlc.trim_lifecycle("TL4494", "4496", "exit @ 5.00, PnL -$40")
events = tlc.get_lifecycle("TL4494")
check("TRIM event appended", len(events) == 3 and events[2]["event"] == "TRIM")

# --- exit ---------------------------------------------------------------
tlc.exit_lifecycle("TL4494", "4496", "PnL -$40, closed")
events = tlc.get_lifecycle("TL4494")
check("EXIT event appended", len(events) == 4 and events[3]["event"] == "EXIT")

# --- shadow -------------------------------------------------------------
tlc.shadow_lifecycle("SIM4495", "4495", "settled @ 7313.50 -> -$560")
events = tlc.get_lifecycle("SIM4495")
check("SHADOW event recorded", len(events) == 1 and events[0]["event"] == "SHADOW")

# --- narrative comment --------------------------------------------------
lc_id = tlc.narrative_lifecycle("4498", "SPX", "SPX closed at 7423.5, $300 on remaining 20%")
check("narrative creates LC-<msg_id>", lc_id == "LC-4498")
events = tlc.get_lifecycle("LC-4498")
check("OPEN event recorded for narrative lifecycle",
      len(events) == 1 and events[0]["event"] == "OPEN")
# add a follow-up comment to the same narrative lifecycle
tlc.comment_lifecycle("LC-4498", "4499", "SPX", "follow-up commentary")
events = tlc.get_lifecycle("LC-4498")
check("COMMENT event appended to narrative lifecycle",
      len(events) == 2 and events[1]["event"] == "COMMENT")

# --- CSV integrity ------------------------------------------------------
rows = list(csv.DictReader(tlc.OUT.open(encoding="utf-8")))
check("CSV has all 7 events", len(rows) == 7)
check("CSV columns match COLS", set(rows[0].keys()) == set(tlc.COLS))

# --- D127: test isolation (the D102/D107 lesson, third instance) ---------
import tempfile as _tf
_orig = tlc.OUT
check("D127: running under a test module", tlc._is_test_run())
# a caller redirect of OUT is honoured as-is
tlc.OUT = pathlib.Path(_tf.mkdtemp()) / "x.csv"
check("D127: caller redirect honoured", tlc._out_path() == tlc.OUT)
# at the DEFAULT path a test run must route to the throwaway file, never live
tlc.OUT = tlc.HERE / "trade_lifecycle.csv"
check("D127: default path in a test run -> .test.csv (never live)",
      "test.csv" in str(tlc._out_path()), str(tlc._out_path()))
tlc.OUT = _orig
# the LIVE archive must carry no fixture lifecycles (D127 purged 576)
if tlc.OUT.exists():
    _ids = {r["lifecycle_id"] for r in csv.DictReader(tlc.OUT.open(encoding="utf-8"))}
    check("D127: no fixture ids in the LIVE archive",
          not ({"TL1", "TL953", "TL961"} & _ids),
          str(sorted({"TL1", "TL953", "TL961"} & _ids)))
else:
    check("D127: no fixture ids in the LIVE archive", True)

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
