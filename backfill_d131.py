#!/usr/bin/env python3
"""backfill_d131.py — orphan/qty repair (2026-11-20). PIPE MUST BE STOPPED.

Repairs the three book-vs-broker gaps the audit surfaced (D131):
  1. TL4483 — D130 2nd instance: broker FILLED 1 spread @8.0 (order 41837390)
     while the fill-gate booked 'cancelled, no position'. Reopen mirroring the
     broker: entry 8.0, D82 synth stop 11.2, open_tradier.
  2. AD17951376140 — the adopted QQQ 9-lot had its qty cut to 1 at boot (old
     cap) so its stop-close (41931908, cancelled; 41932322) covered 1 of 9.
     Restore qty 9; cancel 41932322; manage re-arms a full-size close.
  3. Lifecycle rows for the orphan story (TL4411/TL4403/TL4483 + AD links),
     then run the reconciler (--force) so the SPXW 7465/7480 x9 orphan is
     adopted; LINK each AD row to its source trade.
Idempotent where cheap; run once."""
import csv
import json
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import james_pipe as jp            # noqa: E402
import reconcile_positions as rp   # noqa: E402
import trade_lifecycle as tlc      # noqa: E402

STATE = HERE / "state.json"
LIFE = HERE / "trade_lifecycle.csv"
NOW = time.strftime("%Y-%m-%d %H:%M:%S")


def have_rows():
    seen = set()
    if LIFE.exists():
        for r in csv.DictReader(LIFE.open(encoding="utf-8")):
            seen.add((r["lifecycle_id"], r["msg_id"], r["event"]))
    return seen


def main():
    st = json.loads(STATE.read_text(encoding="utf-8"))
    changed = 0

    # ---- 1. TL4483: reopen mirroring the broker fill --------------------
    t = (st.get("closed") or {}).pop("TL4483", None)
    if t is None and "TL4483" in (st.get("open") or {}):
        t = st["open"]["TL4483"]
        print("TL4483 already open")
    if t is not None:
        t.update({
            "status": "open_tradier", "entry": 8.0, "pnl": None,
            "exit_price": None, "exit_ts": None,
            "stop": jp.synth_spread_stop(t.get("legs") or [], 8.0),
            "fill_note": ("broker FILLED 1 spread @ 8.0 (order 41837390) while the "
                          "fill-gate booked 'cancelled' — reopened to mirror the "
                          "broker (D130 2nd instance / D131 repair)"),
        })
        st.setdefault("open", {})["TL4483"] = t
        changed += 1
        print(f"TL4483 reopened: entry 8.0 stop {t['stop']} "
              f"qty {t['qty']} status {t['status']}")

    # ---- 2. AD17951376140: restore broker qty, drop the 1-lot close -----
    ad = (st.get("open") or {}).get("AD17951376140")
    if ad is not None:
        ad["qty"] = 9
        ad["status"] = "open_tradier"
        ad.pop("close_order_id", None)
        ad.pop("close_reason", None)
        ad["fill_note"] = (ad.get("fill_note") or "") + \
            " | qty restored to 9 — boot cap had cut it to 1 (D131 repair)"
        changed += 1
        print(f"AD17951376140 qty -> 9, close intent re-armed (stop "
              f"{ad.get('stop')})")

    if changed:
        STATE.write_text(json.dumps(st, indent=1), encoding="utf-8")
        print(f"state.json saved ({changed} repaired)")

    # ---- 3. cancel the undersized close ---------------------------------
    try:
        ok, why = jp._TRADIER().cancel("41932322")
        print(f"cancel 41932322: {ok} {why}")
    except Exception as e:                       # noqa: BLE001
        print(f"cancel 41932322 FAILED: {e}")

    # ---- 4. lifecycle rows for the orphan story -------------------------
    have = have_rows()
    rows = [
        ("TL4483", "4483", "OPEN", "SPXW",
         "short 7355/7335P @ 8.0 fill (order 41837390) [D131 repair]", ""),
        ("TL4411", "4411", "OPEN", "QQQ",
         "short 663/661P — order 41785228 filled 10 spreads; book recorded 1 (D72)", ""),
        ("TL4411", "4448", "EXIT", "QQQ",
         "exit[price] (msg 4448) @ 1.24 closed book's 1 lot | 9 lots remained at broker (D131 orphan)", ""),
        ("TL4403", "4403", "OPEN", "SPXW",
         "short 7480/7465P — order 41785313 filled 10 spreads; book recorded 1 (D72)", ""),
        ("TL4403", "4429", "EXIT", "SPXW",
         "exit[price] (msg 4429) @ 15.40 closed book's 1 lot | 9 lots remained at broker (D131 orphan)", ""),
        ("AD17951376140", "", "OPEN", "QQQ",
         "adopted 661/663P x9 from broker — the orphaned remainder of TL4411", ""),
    ]
    added = 0
    with LIFE.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for lc, msg, ev, inst, detail, raw in rows:
            if (lc, msg, ev) in have:
                print(f"skip (exists): {lc} {ev}")
                continue
            w.writerow([NOW, lc, msg, ev, inst, detail, raw])
            added += 1
            print(f"add: {lc:16} {ev:6} {detail[:56]}")
    for lc, to, why in [("AD17951376140", "TL4411",
                         "the 9 orphan lots of TL4411's entry (D72 qty split)")]:
        print("link:", lc, "->", to, tlc.link(lc, to, why))

    # ---- 5. reconciler adopts the SPXW 7465/7480 x9 orphan --------------
    sys.argv.append("--force")
    rc = rp.main()
    print(f"reconcile rc={rc}")

    # link whichever AD rows the adopter just made to their source trades
    st2 = json.loads(STATE.read_text(encoding="utf-8"))
    for tid, tt in (st2.get("open") or {}).items():
        if not tid.startswith("AD") or tid == "AD17951376140":
            continue
        inst = (tt.get("instrument") or "")
        src = "TL4403" if inst == "SPXW" else "TL4411"
        ok = tlc.link(tid, src, "the orphaned remainder of %s's entry (D131)" % src)
        print(f"link: {tid} -> {src} ({ok})")

    print(f"DONE: {added} lifecycle rows, state repaired")


if __name__ == "__main__":
    main()
