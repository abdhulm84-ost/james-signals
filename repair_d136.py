"""repair_d136.py — state+ledger repair for SIM4527's inverted debit sign.

Run ONLY with the pipes stopped (D115): python repair_d136.py
  1. state.json: shadow SIM4527 pnl +1350/MISSED TRADE -> -1350/'ack was right'
  2. signals.csv: one correction SHADOW row so the trail explains the change
"""
import json

import james_pipe as jp

p = "state.json"
st = json.load(open(p, encoding="utf-8"))
sh = (st.get("shadow") or {}).get("SIM4527")
if sh is None:
    raise SystemExit("SIM4527 missing from state — aborting")
if sh.get("pnl") == 1350.0:
    sh["pnl"] = -1350.0
    sh["verdict"] = "ack was right"
    sh["sign_fix"] = ("D136 2026-11-21: debit sign was inverted — booked +1350 "
                      "'MISSED TRADE' on a 13.5 DEBIT that expired worthless at "
                      "SPX 7,175.89; true pnl -1350, acking saved the debit")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    print("SIM4527 repaired: pnl -1350.0, verdict 'ack was right'")
elif sh.get("pnl") == -1350.0:
    print("SIM4527 already repaired")
else:
    raise SystemExit(f"SIM4527 pnl is {sh.get('pnl')!r} — unexpected, aborting")

jp.ledger_row({
    "event": "SHADOW", "trade_id": "SIM4527", "msg_id": "4527",
    "instrument": "SPXW",
    "reason": ("D136 correction: debit sign inverted at settle — booked +1350 "
               "'MISSED TRADE (ack cost us)', truth -1350.00 'ack was right' "
               "(buy 7200C/sell 7220C paid 13.5, expired worthless @ SPX 7,175.89)"),
    "raw": "state repaired while pipes stopped (D115), guard test_d134_status.py"})
print("correction row appended to signals.csv")
