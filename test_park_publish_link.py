"""test_park_publish_link.py — D122 park-outcome publish + D123 lifecycle linking (2026-11-19).

D122: settle_parked previously pushed only a COUNT ("N settled: X won Y lost").
The user's requirement: each parked trade's would-have outcome should land in
Saved Messages PROACTIVELY, without them asking. Now every newly settled park
pushes its own line (msg, structure, credit, close, would-have PnL, verdict).

D123: compound/continuing messages chain onto the thread they continue — a
narrative about an open structure links to that structure's lifecycle
('the remaining 20% of 7315/7325 Bull Puts' -> TL4494). link() is idempotent.

Run: python test_park_publish_link.py   (exit 0 = green)
"""
import csv
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


# ---------------- D122: per-park detail is pushed -----------------------
_T = tempfile.TemporaryDirectory()
tlc_OUT = pathlib.Path(_T.name) / "lc.csv"
_orig_tlc_out = tlc.OUT
tlc.OUT = tlc_OUT

import csv as _csv
# build a synthetic parking_lot with one EXPIRED park
pkl = pathlib.Path(jp.PKL) if hasattr(jp, "PKL") else None
_orig_pkl = pkl
_tmp_pkl = pathlib.Path(_T.name) / "parking_lot.csv"
jp.PKL = _tmp_pkl
_park_cols = getattr(jp, "PARK_COLS", None)
with _tmp_pkl.open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=_park_cols)
    w.writeheader()
    w.writerow({c: "" for c in _park_cols} | {
        "ts": "2026-11-17 10:00:00", "msg_id": "4473", "structure": "vertical_credit",
        "underlying": "QQQ", "body": "665.0", "wing_low": "663.0", "wing_high": "665.0",
        "width": "2.0", "legs": "2", "credit": "1.16",
        "reason": "break-even 663.84 only -3.3 pt from spot 660.54 — thin cushion",
        "raw": "Sell QQQ 665P n Buy 663P and collect around $116"})

# point PARK_OUT at temp too, and stub park_outcome so no network is needed
_orig_park_out = jp.PARK_OUT
jp.PARK_OUT = pathlib.Path(_T.name) / "park_outcomes.csv"
_orig_park_outcome = jp.park_outcome
jp.park_outcome = lambda r: {
    "msg_id": r.get("msg_id"), "park_ts": r.get("ts"),
    "underlying": r.get("underlying"), "structure": r.get("structure"),
    "credit": "1.16", "qty": 1, "expiry": "2026-11-18",
    "settle_px": "659.51", "would_pnl": -84.0,
    "verdict": "would have lost", "reason": r.get("reason"), "raw": r.get("raw")}

PUSHED = []
_orig_push = jp.push
jp.push = lambda text, st: PUSHED.append(text)
jp.log = lambda m: None
jp.save_state = lambda st: None

try:
    n = jp.settle_parked(st={})
finally:
    jp.push = _orig_push
    jp.PKL = _orig_pkl
    jp.PARK_OUT = _orig_park_out
    jp.park_outcome = _orig_park_outcome

check("settle_parked settled the park", n == 1, str(n))
check("a push fired at settlement", len(PUSHED) >= 1, str(len(PUSHED)))
_push = PUSHED[-1] if PUSHED else ""
check("push contains the per-park outcome line (msg id)", "msg4473" in _push, _push[:90])
check("push names the verdict", "would have lost" in _push, _push[:140])
check("push carries the would-have PnL", "-$84" in _push or "$-84" in _push, _push[:170])

# ---------------- D123: relationship linking ----------------------------
tlc.open_lifecycle("TL4494", "4494", "SPXW", "sell 7325P/buy 7315P @ 4.60")
lc = tlc.narrative_lifecycle("4498", "SPXW", "narrative: $300 on remaining 20%")
check("find_related matches the prior lifecycle",
      tlc.find_related("SPXW", exclude_id=lc) == "TL4494",
      str(tlc.find_related("SPXW", exclude_id=lc)))
check("find_related refuses a different instrument",
      tlc.find_related("QQQ", exclude_id=lc) is None)
linked = tlc.related_and_link(lc, "SPXW", "remaining 20% of 7315/7325")
check("related_and_link links to TL4494", linked == "TL4494", str(linked))
links = [e for e in tlc.get_lifecycle(lc) if e.get("event") == "LINK"]
check("one LINK row after one call", len(links) == 1, str(len(links)))
tlc.related_and_link(lc, "SPXW", "repeat — dedupes")
links = [e for e in tlc.get_lifecycle(lc) if e.get("event") == "LINK"]
check("link is idempotent (still 1)", len(links) == 1, str(len(links)))
# self-link must be a no-op (no LINK row pointing at itself)
tlc.link(lc, lc, "self")
links = [e for e in tlc.get_lifecycle(lc) if e.get("event") == "LINK"]
check("self-link never recorded", len(links) == 1, str(len(links)))

tlc.OUT = _orig_tlc_out
_T.cleanup()
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
