"""reconcile_positions.py — BOOK vs BROKER position drift guard.

The book said BLK was open for four days on a position that never existed (its
entry order expired unfilled). This compares the live sandbox account against
state.json and reports three classes:

  PHANTOM   book says open, broker has nothing  (auto-voided when the entry never
            filled; otherwise flagged for a human)
  UNBOOKED  broker holds it, book does not      (the stray-condor class)
  DRIFT     both know it, quantities disagree   (flagged only)

Never closes anything by itself: a wrong close costs more than a wrong row.
Findings go to the ledger (POS_DRIFT), position_drift.csv and a Telegram card.
Run: python reconcile_positions.py [--force]      (0 clean / 1 findings / 2 closed)
"""
import csv
import datetime
import json
import sys
import time
import urllib.request

import james_pipe as jp

OUT = jp.HERE / "position_drift.csv"
COLS = ["ts", "kind", "trade_id", "symbol", "book_qty", "broker_qty", "note"]


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def market_open():
    n = utc_now()
    return n.weekday() < 5 and (13, 25) <= (n.hour, n.minute) < (21, 5)


def broker_positions():
    from james_exec import TradierConfig
    cfg = TradierConfig.load("sandbox")
    req = urllib.request.Request(
        f"{cfg.base_url}/accounts/{cfg.account_id}/positions",
        headers={"Authorization": f"Bearer {cfg.access_token}",
                 "Accept": "application/json"})
    j = json.loads(urllib.request.urlopen(req, timeout=20).read())
    out = {}
    for p in ((j.get("positions") or {}).get("position") or []):
        sym = str(p.get("symbol") or "").upper()
        try:
            q = int(float(p.get("quantity") or 0))
            cost = float(p.get("cost_basis") or 0.0)
        except (TypeError, ValueError):
            continue
        if sym and q:
            out[sym] = (q, cost)
    return out


def _occ(s):
    return str(s or "").upper().replace(" ", "")


def book_expect(st):
    """occ/stock -> (signed qty, trade id), from the book's own legs."""
    exp, owner = {}, {}
    for tid, t in st.get("open", {}).items():
        q = int(t.get("qty") or 0)
        if not q:
            continue
        sign = -1 if t.get("direction") == "short" else 1
        if t.get("executor") == "tradier_stock":
            sym = str(t.get("symbol") or "").upper()
            exp[sym] = exp.get(sym, 0) + sign * q
            owner[sym] = tid
            continue
        legs = t.get("legs") or []
        if t.get("opt_id") and not legs:                 # single option
            sym = _occ(t["opt_id"])
            exp[sym] = exp.get(sym, 0) + sign * q
            owner[sym] = tid
            continue
        root = _occ(t.get("opt_underlying") or t.get("symbol")
                    or t.get("instrument") or "")
        expi = str(t.get("expiry") or t.get("opt_expiry") or "").replace("-", "")[2:]
        for l in legs:
            try:
                strike, right, action = l[0], l[1], l[2]
            except (TypeError, IndexError):
                continue
            cp = "C" if str(right).lower().startswith("c") else "P"
            sym = f"{root}{expi}{cp}{int(round(float(strike) * 1000)):08d}"
            leg_q = -q if str(action).lower().startswith("s") else q
            exp[sym] = exp.get(sym, 0) + leg_q
            owner[sym] = tid
    return {k: v for k, v in exp.items() if v}, owner


def terminal_unfilled(t):
    note = str(t.get("fill_note") or "").lower()
    return any(x in note for x in ("expired", "rejected", "never filled",
                                   "no position", "cancelled", "cancel"))


def void(t, why, st):
    t["pnl"], t["status"] = 0.0, "closed"
    t["exit_price"], t["exit_ts"] = t.get("entry"), jp.now()
    t["fill_note"] = why
    st.setdefault("closed", {})[t["id"]] = t
    st.get("open", {}).pop(t["id"], None)
    jp.ledger_row({**t, "event": "EXIT", "reason": why})


def save_merged(st):
    disk = jp.load(jp.STATE, {})
    for k, v in st.get("open", {}).items():
        disk.setdefault("open", {})[k] = v
    for k, v in st.get("closed", {}).items():
        disk.setdefault("closed", {})[k] = v
        disk.get("open", {}).pop(k, None)
    jp.save_state(disk)


def append_csv(rows):
    new = not OUT.exists()
    with OUT.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(COLS)
        for r in rows:
            w.writerow([r.get(c, "") for c in COLS])


def _parse_occ(sym):
    """'SPXW261105C07700000' -> (root, expiry, right, strike) or None."""
    if len(sym) < 16 or sym[-9] not in "CP":
        return None
    try:
        return sym[:-15], sym[-15:-9], sym[-9], int(sym[-8:]) / 1000.0
    except ValueError:
        return None


def adopt(unbooked, st):
    """Re-book broker positions the book lost — pairs (same root+expiry+right,
    opposite signs) or singles. Basis comes from the broker's cost_basis.
    Nothing is closed: the positions keep being managed."""
    groups = {}
    for sym, q, cost in unbooked:
        p = _parse_occ(sym)
        if not p:
            continue
        groups.setdefault((p[0], p[1], p[2]), []).append((sym, q, cost, p[3]))
    made = []
    _today = jp.us_today().strftime("%y%m%d")
    for (root, expi, right), rows in groups.items():
        if not rows or len(rows) > 2:
            continue
        # D131: never adopt a DEAD OCC — expired legs can linger at the broker
        # for hours past expiry while settlement clears them (the SPXW 261119C
        # trio). An expired option cannot be managed or closed; adopting it
        # books a position that can only ever be a phantom.
        if str(expi) < _today:
            jp.log(f"adopt: {root}{expi}{right} expired ({expi} < {_today}) — skipped")
            continue
        qty = min(abs(r[1]) for r in rows)
        if qty < 1:
            continue
        if len(rows) == 2 and (rows[0][1] > 0) == (rows[1][1] > 0):
            continue
        net = sum(r[2] for r in rows)          # >0 paid (debit), <0 received
        entry = round(abs(net) / (100.0 * qty), 4)
        direction = "short" if net < 0 else "long"
        tid = f"AD{int(time.time())}{len(made)}"
        legs = [(r[3], "call" if right == "C" else "put",
                 "sell" if r[1] < 0 else "buy") for r in rows]
        pct = jp.CFG["default_stop_pct"] / 100
        stop = round(entry * (1 - pct), 2) if direction == "long"             else round(entry * (1 + pct), 2)
        t = {"id": tid, "msg_id": "", "instrument": root, "symbol": root,
             "opt_id": (f"{root} {expi}{right}{int(round(legs[0][0] * 1000)):08d}"
                        if len(legs) == 1 else ""),
             "direction": direction, "entry": entry, "qty": qty,
             "stop": stop, "targets": [], "confidence": None,
             "horizon": "swing", "rationale": "adopted from broker reconcile",
             "k": 1.0, "exit_price": None, "exit_ts": None,
             "structure": "vertical" if len(legs) > 1 else "single",
             "naked": (direction == "short" and len(legs) == 1),
             "expiry": f"20{expi[:2]}-{expi[2:4]}-{expi[4:6]}",
             "opt_underlying": root, "legs": legs,
             "status": "open_tradier", "executor": "tradier",
             "fill_note": "adopted from broker reconcile — book had lost it",
             "opened": jp.now(), "pnl": None,
             "tier": "middle" if len(legs) > 1
                     else ("high" if direction == "short" else "middle")}
        st.setdefault("open", {})[tid] = t
        jp.ledger_row({"event": "ADOPT", "msg_id": "", "instrument": root,
                       "reason": f"adopted {len(legs)}-leg {direction} @ {entry} "
                                 f"qty {qty} — broker had it, book did not"})
        # D131: an adopted trade is still a trade story — give it an OPEN row
        # so its later EXIT can be linked/read like any other.
        jp._lc_start(tid, "", root,
                     f"adopted {len(legs)}-leg {direction} @ {entry} qty {qty} "
                     f"from broker reconcile (book had lost it)")
        made.append((tid, t))
    return made


def main():
    if "--force" not in sys.argv and not market_open():
        print("market closed — nothing to compare")
        return 2
    st = jp.load(jp.STATE, {})
    try:
        broker = broker_positions()
    except Exception as e:
        jp.push(f"⚠️ POSITION RECONCILE failed to read broker: {e}", st)
        return 1
    exp, owner = book_expect(st)
    phantoms, unbooked, drift, seen_ph = [], [], [], set()
    for sym, q in exp.items():
        bqr = broker.get(sym)
        bq = bqr[0] if bqr else None
        tid = owner.get(sym)
        st_t = st.get("open", {}).get(tid) or {}
        if bq is None and tid in st.get("open", {}) \
                and st_t.get("status") not in ("pending", "pending_entry"):
            if tid not in seen_ph:
                seen_ph.add(tid)
                phantoms.append((tid, st_t, sym))
        elif bq is not None and int(bq) != int(q):
            drift.append((sym, q, bq))
    for sym, (bq, cost) in broker.items():
        if sym not in exp:
            unbooked.append((sym, bq, cost))

    ts = jp.now()
    rows, notes = [], []
    for tid, t, sym in phantoms:
        if terminal_unfilled(t):
            void(t, f"phantom: book had {t['instrument']} but the broker holds no "
                     f"{sym} and the entry never filled — voided", st)
            rows.append({"ts": ts, "kind": "PHANTOM_VOIDED", "trade_id": tid,
                         "symbol": sym, "book_qty": t.get("qty"), "broker_qty": 0,
                         "note": "entry never filled"})
            notes.append(f"🧹 VOIDED {tid} {t['instrument']} — no broker position, "
                         f"entry never filled")
        else:
            rows.append({"ts": ts, "kind": "PHANTOM", "trade_id": tid,
                         "symbol": sym, "book_qty": t.get("qty"), "broker_qty": 0,
                         "note": "position missing at broker"})
            notes.append(f"👻 PHANTOM {tid} {t['instrument']} — book open, broker flat")
    for sym, q, bq in drift:
        rows.append({"ts": ts, "kind": "DRIFT", "symbol": sym, "book_qty": q,
                     "broker_qty": bq, "note": "quantity mismatch"})
        notes.append(f"⚖️ DRIFT {sym} — book {q}, broker {bq}")
    adopted = []
    if "--no-adopt" not in sys.argv and unbooked:
        adopted = adopt(unbooked, st)
        for tid, t in adopted:
            notes.append(f"🪢 ADOPTED {tid} {t['instrument']} — book had lost a live "
                         f"position; now managed")
            rows.append({"ts": ts, "kind": "ADOPTED", "trade_id": tid,
                         "symbol": t.get("symbol"), "book_qty": t.get("qty"),
                         "broker_qty": t.get("qty"), "note": t["fill_note"]})
    adopted_syms = set()
    for _tid, t in adopted:
        if t.get("opt_id"):
            adopted_syms.add(_occ(t["opt_id"]))
        root = _occ(t.get("opt_underlying") or t.get("symbol"))
        expi = str(t.get("expiry") or "").replace("-", "")[2:]
        for l in t.get("legs") or []:
            cp = "C" if str(l[1]).lower().startswith("c") else "P"
            adopted_syms.add(f"{root}{expi}{cp}{int(round(float(l[0]) * 1000)):08d}")
    for sym, q, _c in unbooked:
        if sym in adopted_syms:
            continue
        rows.append({"ts": ts, "kind": "UNBOOKED", "symbol": sym, "book_qty": 0,
                     "broker_qty": q, "note": "broker position not in book"})
        notes.append(f"📕 UNBOOKED {sym} qty {q} — at broker, in no book")

    if rows or adopted:
        append_csv(rows)
        for r in rows:
            jp.ledger_row({"event": "POS_DRIFT", "msg_id": "", "instrument": r["symbol"],
                           "reason": f"{r['kind']}: book {r['book_qty']} / broker "
                                     f"{r['broker_qty']} — {r['note']}"})
        jp.push("🧭 POSITION RECONCILE\n" + "\n".join(notes), st)
        save_merged(st)
        print("\n".join(notes))
        return 0
    print(f"clean — {len(exp)} book symbol(s) match the broker")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
