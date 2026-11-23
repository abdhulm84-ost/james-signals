"""open_reconcile.py — resolve the two close orders that were stuck PendingNew
for three days (BLK 40909802, PLTR 40909883) once the US session opens.

BLK: its ENTRY order (40870448) expired unfilled, so there is NO BLK position —
the resting close is a phantom. If it fills anyway it creates a spread that must
be closed at once; if it rejects/expires the book entry is simply voided.
PLTR: a real 5-lot is open and the resting close covers only 1 of 5 — book the
partial at its fill and close the remaining 4.

Run at/after the open:  python open_reconcile.py [--force]
"""
import sys
import time
import datetime

import james_pipe as jp


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def market_open():
    n = utc_now()
    return n.weekday() < 5 and (13, 31) <= (n.hour, n.minute) < (21, 0)


def status_of(oid):
    s, fill = jp._tradier_close_status(oid)
    return (s or "").upper(), fill


def positions_of(sym):
    import json
    import urllib.request
    from james_exec import TradierConfig
    cfg = TradierConfig.load("sandbox")
    req = urllib.request.Request(
        f"{cfg.base_url}/accounts/{cfg.account_id}/positions",
        headers={"Authorization": f"Bearer {cfg.access_token}",
                 "Accept": "application/json"})
    j = json.loads(urllib.request.urlopen(req, timeout=15).read())
    return [p for p in ((j.get("positions") or {}).get("position") or [])
            if str(p.get("symbol", "")).upper().startswith(sym.upper())]


def save_merged(st):
    """The pipe may save while we run: re-read and merge ONLY our two ids so a
    full-file overwrite cannot drop newer pipe state."""
    disk = jp.load(jp.STATE, {})
    for k, v in st.get("open", {}).items():
        if k in ("TL3731", "TL3750"):
            disk.setdefault("open", {})[k] = v
        else:
            disk.setdefault("open", {})[k] = disk.get("open", {}).get(k, v)
    for k, v in st.get("closed", {}).items():
        disk.setdefault("closed", {})[k] = v
        disk.get("open", {}).pop(k, None)
    jp.save_state(disk)


def book_zero(t, note, st):
    t["pnl"], t["status"] = 0.0, "closed"
    t["exit_price"], t["exit_ts"] = t.get("entry"), jp.now()
    t["fill_note"] = note
    st.setdefault("closed", {})[t["id"]] = t
    st.get("open", {}).pop(t["id"], None)
    jp.ledger_row({**t, "event": "EXIT", "reason": note})


def main():
    if "--force" not in sys.argv and not market_open():
        print("market not open — rerun at the US open or pass --force")
        return 2
    st = jp.load(jp.STATE, {})
    out = []
    blk = st.get("open", {}).get("TL3731")
    if blk and blk.get("status") == "close_pending":
        su, fill = status_of(blk.get("close_order_id"))
        if "FILL" in su and fill:
            blk["close_qty"] = blk.get("qty") or 1
            jp.book_tradier_close(blk, fill, st)
            pos = positions_of(blk["instrument"])
            if pos:
                adj = dict(blk, id="BLKADJ", status="open_tradier")
                jp.close_trade(adj, 0.0, "BLK phantom close filled — flatten", st)
                out.append(f"BLK close FILLED with no position — flattening {pos}")
            else:
                out.append("BLK close filled; no position detected")
        elif any(x in su for x in ("REJECT", "EXPIRE", "CANCEL", "FAIL")):
            book_zero(blk, f"BLK close {su}, entry never filled — phantom voided", st)
            out.append(f"BLK phantom close {su} — book entry voided")
        else:
            out.append(f"BLK close still {su or 'unknown'} — untouched")
    pltr = st.get("open", {}).get("TL3750")
    if pltr and pltr.get("status") == "close_pending":
        su, fill = status_of(pltr.get("close_order_id"))
        if "FILL" in su and fill:
            pltr["close_qty"] = min(1, pltr.get("qty") or 1)
            jp.book_tradier_close(pltr, fill, st)
            left = pltr.get("qty") or 0
            if left:
                jp.close_trade(pltr, 0.0, "close remaining after partial fill", st)
            out.append(f"PLTR partial filled @ {fill}; remaining {left} re-closed")
        elif any(x in su for x in ("REJECT", "EXPIRE", "CANCEL", "FAIL")):
            n = pltr.get("qty") or 1
            jp.close_trade(pltr, 0.0, f"stale close {su} — fresh close of {n}", st)
            out.append(f"PLTR close {su} — fresh close sent for {n}")
        else:
            out.append(f"PLTR close still {su or 'unknown'} — untouched")
    save_merged(st)
    msg = "🧭 OPEN RECONCILE\n" + ("\n".join(out) if out else "nothing to do")
    jp.push(msg, st)
    print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
