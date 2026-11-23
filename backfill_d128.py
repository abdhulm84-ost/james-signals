#!/usr/bin/env python3
"""backfill_d128.py — lifecycle + relationship backfill for the 4504-4514
audit (2026-11-20). Run with the PIPE STOPPED (D115): it appends to
trade_lifecycle.csv and edits state.json.

What it repairs (all D128-class, none touch trades or ledgers):
  * OPEN rows for trades opened before the fix landed (O/B ids, TL4415/4455)
  * EXIT rows for every 11-19 close that landed in an async booking path
  * the LC-4506 hold comment + its LINK into the SPXW bear-call thread
  * LINK rows chaining the MRNA narratives (LC-4511/4512) to their trade
  * TL4500/TL4503 state repair (D130): the fill-gate booked them "cancelled,
    PnL 0" while the broker FILLED both spreads and held them to expiry —
    SPX settled 7175.83 on 11-19, both spreads expired worthless, credit
    kept: +$1,180 / +$560 (order fills -11.8 / -5.6, qty 1)
Idempotent: rows that already exist are skipped (event-keyed).
"""
import csv
import json
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import trade_lifecycle as tlc   # noqa: E402  (pipe stopped: live archive is the target)

LIFE = HERE / "trade_lifecycle.csv"
STATE = HERE / "state.json"
NOW = time.strftime("%Y-%m-%d %H:%M:%S")

# (lifecycle_id, msg_id, event, instrument, detail, raw)
ROWS = [
    # ---- OPEN rows for trades opened before the D128 fix -----------------
    ("TL4415", "4415", "OPEN", "SOXL",
     "short 50P/45P @ 2.09 credit [backfill D128]", ""),
    ("TL4455", "4455", "OPEN", "NVDA",
     "short 155P/150P @ 2.40 credit [backfill D128]", ""),
    ("O1795099547", "4510", "OPEN", "MRNA",
     "long 192.5P 11-20 filled @ 4.50, order 41890803 [backfill D128]", ""),
    ("O1795099942", "4514", "OPEN", "FUTU",
     "long 88C 11-20 filled @ 1.49, order 41892131 [backfill D128]", ""),
    ("B1795098590", "4504", "OPEN", "NDX100",
     "bracket triggered: short 27579.4, order via bb [backfill D128]", ""),
    # ---- EXIT rows for 11-19 closes that skipped the lifecycle -----------
    # msg_id convention: the pipe records the trade's ORIGIN msg (t["msg_id"]),
    # the closing message is named in the detail.
    ("TL4415", "4415", "EXIT", "SOXL",
     "book gains (msg 4507) @ fill 1.46 | PnL +63.0 [backfill D128]", ""),
    ("TL4455", "4455", "EXIT", "NVDA",
     "book gains (msg 4508) @ fill 2.55 | PnL -15.0 [backfill D128]", ""),
    ("O1795099547", "4510", "EXIT", "MRNA",
     "book gains (msg 4513) @ fill 1.41 | PnL -309.0 [backfill D128]", ""),
    ("O1795099942", "4514", "EXIT", "FUTU",
     "target hit (2.44 >= 1.81) @ fill 1.42 | PnL -7.0 [backfill D128]", ""),
    ("B1795098590", "4504", "EXIT", "NDX100",
     "target hit (27458.40 <= 27500.00), recovered from deal history "
     "close 27750.00 | PnL -17.06 [backfill D128]", ""),
    ("TL4520", "4520", "EXIT", "SPXW",
     "stop hit (15.00 >= 10.36) @ fill 8.20 | PnL -80.0 [backfill D128]", ""),
    ("TL4522", "4522", "EXIT", "SPXW",
     "stop hit (11.90 >= 9.24) @ fill 7.10 | PnL -50.0 [backfill D128]", ""),
    ("TL4529", "4529", "EXIT", "QQQ",
     "stop hit (1.57 >= 1.23) @ fill 0.95 | PnL -7.0 [backfill D128]", ""),
    ("TL4523", "4523", "EXIT", "SPXW",
     "entry order EXPIRED — no position [backfill D128]", ""),
    ("TL4524", "4524", "EXIT", "SPXW",
     "entry order EXPIRED — no position [backfill D128]", ""),
    ("TL4500", "4500", "EXIT", "SPXW",
     "broker FILLED 7365C/7385C @ credit 11.8 (order 41877475); expired "
     "worthless 11-19, SPX settle 7175.83 — credit kept, PnL +1180 "
     "[D130 repair backfill]", ""),
    ("TL4503", "4503", "EXIT", "SPXW",
     "broker FILLED 7375C/7385C @ credit 5.6 (order 41877497); expired "
     "worthless 11-19, SPX settle 7175.83 — credit kept, PnL +560 "
     "[D130 repair backfill]", ""),
    # ---- the hold message that never became a row ------------------------
    ("LC-4506", "4506", "COMMENT", "SPXW",
     "hold: Holding bear calls (family-instrument inference, D128c backfill)",
     "Holding bear calls"),
]

# (lifecycle_id, to_id, reason) — explicit links the fixed code would now write
LINKS = [
    ("LC-4506", "TL4503", "hold on the SPXW bear calls of msgs 4500/4503"),
    ("LC-4511", "O1795099547", "MRNA narrative describes the open 192.5P (msg 4510)"),
    ("LC-4512", "LC-4511", "'80% up' continues the MRNA thread of msg 4511"),
]


def have_rows():
    seen = set()
    if LIFE.exists():
        for r in csv.DictReader(LIFE.open(encoding="utf-8")):
            seen.add((r["lifecycle_id"], r["msg_id"], r["event"]))
    return seen


def backfill_lifecycle():
    have = have_rows()
    added = 0
    with LIFE.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for lc, msg, ev, inst, detail, raw in ROWS:
            if (lc, msg, ev) in have:
                print(f"skip (exists): {lc} {ev}")
                continue
            w.writerow([NOW, lc, msg, ev, inst, detail, raw])
            added += 1
            print(f"add: {lc:14} {ev:8} {detail[:64]}")
    # links after all rows exist (find/link reads the file)
    for lc, to, why in LINKS:
        try:
            if tlc.link(lc, to, why):
                print(f"link: {lc} -> {to} ({why[:48]})")
            else:
                print(f"link skipped: {lc} -> {to}")
        except Exception as e:
            print(f"link FAILED {lc}->{to}: {e}")
    return added


def repair_state():
    st = json.loads(STATE.read_text(encoding="utf-8"))
    fixes = {
        "TL4500": {"pnl": 1180.0, "entry": 11.8, "exit_price": 0.0,
                   "exit_ts": "2026-11-19 21:00:00",
                   "fill_note": "broker FILLED (41877475, credit 11.8) — fill-gate "
                                "phantom-cancel corrected (D130); expired worthless "
                                "11-19 vs SPX settle 7175.83, credit kept"},
        "TL4503": {"pnl": 560.0, "entry": 5.6, "exit_price": 0.0,
                   "exit_ts": "2026-11-19 21:00:00",
                   "fill_note": "broker FILLED (41877497, credit 5.6) — fill-gate "
                                "phantom-cancel corrected (D130); expired worthless "
                                "11-19 vs SPX settle 7175.83, credit kept"},
    }
    changed = 0
    for tid, fix in fixes.items():
        t = (st.get("closed") or {}).get(tid)
        if t is None:
            print(f"state: {tid} not in closed — SKIPPED")
            continue
        if "D130" in str(t.get("fill_note") or ""):
            print(f"state: {tid} already repaired")
            continue
        before = (t.get("pnl"), t.get("fill_note", "")[:40])
        t.update(fix)
        t["status"] = "closed"
        changed += 1
        print(f"state: {tid} pnl {before[0]} -> {fix['pnl']} | {before[1]!r} -> repaired")
    if changed:
        STATE.write_text(json.dumps(st, indent=1), encoding="utf-8")
        print(f"state.json saved ({changed} repaired)")
    return changed


if __name__ == "__main__":
    n = backfill_lifecycle()
    m = repair_state()
    print(f"DONE: {n} lifecycle rows appended, {m} state rows repaired")
