"""test_lifecycle_d128.py — D128 lifecycle completeness + D129 fill-divergence
+ D130 fill-gate phantom cancel (2026-11-20).

D128 (from the 4504-4514 audit): three gaps meant the archive could never
tell a trade's story —
  a) every trade_lifecycle writer returned None, so comment_lifecycle's id
     never reached related_and_link and link() refused the falsy id: the
     archive had ZERO LINK rows while the log claimed "lifecycle None linked"
  b) only the multileg path wrote OPEN rows (O/B/TB/sim ids had none) and
     only the sync Tiger tail wrote EXIT (async Tradier closes, option close,
     BB close, manage retires all skipped) — so MRNA/FUTU/SOXL/NVDA/NDX
     closes had no lifecycle story
  c) 'hold' class was excluded from the comment hook and 4506 named no
     ticker — with a unique family match ('bear calls' -> one instrument in
     the book) the thread can now be identified

D129: an exit fill far from the mark the decision was made on (FUTU mark
2.44 -> fill 1.42) now pushes a warning — alert only, never blocks.

D130: the D104 fill-gate branch only runs when the order ALREADY FILLED,
yet it booked "cancelled, no position" without checking — TL4500/4503 sat
at the broker all evening (reconcile flagged UNBOOKED) while the book was
flat. Now it books the REAL position with a stop.

Run: python test_lifecycle_d128.py   (exit 0 = green)
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import james_pipe as jp
import trade_lifecycle as tlc

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


# --- isolated lifecycle archive ------------------------------------------
_T = tempfile.TemporaryDirectory()
_orig_out = tlc.OUT
tlc.OUT = pathlib.Path(_T.name) / "lc.csv"

try:
    # a) writers return their ids and links are actually recorded
    cid = tlc.comment_lifecycle("LC-1", "9001", "MRNA", "narrative: dropping big")
    check("comment_lifecycle returns its id", cid == "LC-1", str(cid))
    oid = tlc.open_lifecycle("O-TEST", "9002", "MRNA", "filled @ 4.50")
    check("open_lifecycle returns its id", oid == "O-TEST", str(oid))
    check("exit_lifecycle returns its id",
          tlc.exit_lifecycle("O-TEST", "9003", "closed @ 1.41", "", "MRNA") == "O-TEST")
    check("trim_lifecycle returns its id",
          tlc.trim_lifecycle("O-TEST", "9004", "trimmed 1 of 2") == "O-TEST")

    linked = tlc.related_and_link(cid, "MRNA", "narrative follows the trade",
                                  msg_id="9001", src_ts="2026-11-19 14:02:00")
    check("related_and_link returns the target", linked == "O-TEST", str(linked))
    links = [e for e in tlc.get_lifecycle("LC-1") if e.get("event") == "LINK"]
    check("LINK row actually written", len(links) == 1, str(links))
    check("LINK detail points at the trade", links and "-> O-TEST" in links[0]["detail"],
          str(links))
    # 2026-11-20 user note: the relationship captures WHO/WHEN established it
    check("LINK carries the linking msg id", links and links[0]["msg_id"] == "9001",
          str(links[0] if links else None))
    check("LINK captures the source timestamp",
          links and "[src 2026-11-19 14:02:00]" in links[0]["detail"],
          str(links[0] if links else None))

    # idempotency + refusal paths keep their contracts
    tlc.related_and_link(cid, "MRNA", "repeat")
    links = [e for e in tlc.get_lifecycle("LC-1") if e.get("event") == "LINK"]
    check("repeat link deduped", len(links) == 1, str(len(links)))
    check("link(None) refused", tlc.link(None, "X") is False)
    check("self-link refused", tlc.link(cid, cid) is False)
    check("related_and_link with no target returns None",
          tlc.related_and_link("LC-2", "ZZZZ", "no such instrument") is None)
    check("related_and_link with falsy id writes nothing",
          tlc.related_and_link(None, "MRNA", "broken") is None
          and not any(e["lifecycle_id"] == "None" and e["event"] == "LINK"
                      for e in tlc.get_lifecycle("LC-1")))
finally:
    tlc.OUT = _orig_out
    _T.cleanup()

# --- source-level guards (the pipe must call the hooks) -------------------
_src = pathlib.Path(jp.__file__).read_text(encoding="utf-8", errors="replace")


def _fn_body(name):
    i = _src.index(f"def {name}(")
    j = _src.find("\ndef ", i + 1)
    return _src[i:j if j != -1 else len(_src)]


check("open_option_trade writes OPEN rows (O-ids)",
      "_lc_start(trade_id, msg_id" in _fn_body("open_option_trade"))
check("open_trade_bb writes OPEN rows (B-ids)",
      "_lc_start(trade_id, msg_id" in _fn_body("open_trade_bb"))
check("tradier stock opener writes OPEN rows (TB-ids)",
      "_lc_start(trade_id, msg_id" in _fn_body("open_trade_tradier_stock"))
check("book_tradier_close writes EXIT/TRIM + divergence check",
      '_lc_finish(t, "EXIT"' in _fn_body("book_tradier_close")
      and '_lc_finish(t, "TRIM"' in _fn_body("book_tradier_close")
      and "_check_exit_diverge" in _fn_body("book_tradier_close"))
check("book_option_close writes EXIT/TRIM + divergence check",
      "_lc_finish" in _fn_body("book_option_close")
      and "_check_exit_diverge" in _fn_body("book_option_close"))
check("close_trade_bb writes EXIT rows",
      '_lc_finish(t, "EXIT"' in _fn_body("close_trade_bb"))
check("fill-gate books the REAL position (D130, no phantom cancel)",
      "D130" in _fn_body("manage_once")
      and "already FILLED" in _fn_body("manage_once"))
check("manage_once retires write EXIT rows",
      '_lc_finish(t, "EXIT"' in _fn_body("manage_once"))
check("hold class joins the comment hook (D128c)",
      '"hold"' in _src[_src.index("D119: lifecycle — narrative"):
                       _src.index("D119: lifecycle — narrative") + 1800])
check("family fallback exists for instrument-less holds",
      "len(_cands) == 1" in _src)
check("execute_exit_action stashes the decision mark (D129)",
      '_exit_mark' in _fn_body("execute_exit_action"))
check("check_exit_diverge is warn-only (no raise)",
      "push(" in _fn_body("_check_exit_diverge")
      and "raise" not in _fn_body("_check_exit_diverge"))

# --- D129 behaviour: divergence warns, aligned fills stay quiet ----------
PUSHED = []
_orig_push, _orig_log = jp.push, jp.log
jp.push = lambda text, st: PUSHED.append(text)
jp.log = lambda m: None
jp.ledger_row = lambda r: None
jp.save_state = lambda st: None
try:
    t_far = {"id": "TFAR", "msg_id": "9100", "instrument": "FUTU 88C 11-20",
             "direction": "long", "entry": 1.49, "qty": 1, "status": "open_tradier",
             "exit_price": None, "exit_ts": None, "pnl": None, "fill_note": "",
             "_exit_mark": 2.44}
    st = {"open": {"TFAR": t_far}, "closed": {}}
    jp.book_tradier_close(t_far, 1.42, st)
    check("fill 42% below decision mark pushes a D129 warning",
          any("D129" in p for p in PUSHED), str(PUSHED))
    check("_exit_mark popped after check", "_exit_mark" not in t_far)
    check("trade booked closed normally despite divergence", t_far["status"] == "closed")

    PUSHED.clear()
    t_ok = {"id": "TOK", "msg_id": "9101", "instrument": "NVDA 155P",
            "direction": "short", "entry": 2.4, "qty": 1, "status": "open_tradier",
            "exit_price": None, "exit_ts": None, "pnl": None, "fill_note": "",
            "_exit_mark": 2.6}
    st2 = {"open": {"TOK": t_ok}, "closed": {}}
    jp.book_tradier_close(t_ok, 2.55, st2)
    check("fill within 30% of mark pushes nothing",
          not any("D129" in p for p in PUSHED), str(PUSHED))
    check("aligned close books PnL", t_ok.get("pnl") == -15, str(t_ok.get("pnl")))
finally:
    jp.push, jp.log = _orig_push, _orig_log
    jp.ledger_row = lambda r: None  # left stubbed for any late calls

# --- D128c: family fallback picks the unique instrument ------------------
jp.push = lambda t, s: None
st_book = {"open": {
    "TL-A": {"id": "TL-A", "instrument": "SPXW", "status": "open_tradier",
             "legs": [[7365.0, "call", "sell"], [7385.0, "call", "buy"]]},
    "TL-B": {"id": "TL-B", "instrument": "SOXL", "status": "open_tradier",
             "legs": [[50.0, "put", "sell"], [45.0, "put", "buy"]]},
}}
import re as _re
_fam = jp._structure_family("Holding bear calls")
_cands = {}
for _pt in st_book["open"].values():
    if jp._fam_ok(_pt, _fam):
        _ci = jp._canon_underlying(_pt.get("instrument"))
        if _ci:
            _cands[_ci] = _pt.get("instrument")
check("'bear calls' family resolves to SPXW uniquely",
      len(_cands) == 1 and next(iter(_cands.values())) == "SPXW", str(_cands))
_fam2 = jp._structure_family("holding the spreads") or None
check("no family -> no inference", not _fam2, str(_fam2))

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
