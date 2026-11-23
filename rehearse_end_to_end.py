#!/usr/bin/env python3
"""rehearse_end_to_end.py — the collective proof: every REAL message through
the FULL decision chain (parse -> gates -> route) in capture-only mode.

The worry it answers (user, 2026-11-13): after 47 defect fixes, do the pieces
TOGETHER convert live messages into the right trades? This replays the real
message corpus (spx_window_raw.txt) against the current code and scores each
disposition against the audit's own verdicts (spx_audit_verdicts.csv):

  TRADE   — a placement was attempted (structure captured)
  PARK    — park_signal called (reason captured)
  RECORD  — ledger-only (narrative/level/prediction/exit) or silence

The headline numbers: 0 crashes (a bad message must never kill the chain),
OVERTRADES (we trade what the audit says must NOT be traded — the worst
category: a wrong trade is worse than a missed one), and MISSED conversions.

Caveat printed in the output: gate outcomes (ITM/cushion) use stub spots, so
guard hits can differ from live by spot; the disposition CLASS is the score.

Run: python rehearse_end_to_end.py   (exit 1 only on crashes)
"""
import csv
import datetime as _dt
import re
import sys
import traceback

import james_pipe as jp

# ---- capture-only world -------------------------------------------------
PLACED = []
PARKS = []
LEDGER = []
PUSHES = []
ST = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}


class _Exec:
    def place(self, rec):
        PLACED.append(rec)

        class R:
            ok = True
            order_id = "R"
            venue = "tradier"
            reason = "rehearsal"
            fill_price = None
        return R()

    def quote_stock(self, sym):
        return 50.0

    def place_stock(self, sym, side, qty):
        return self.place(None)

    def order_fill(self, o):
        return "filled", 1.0

    def cancel(self, o):
        return True, "x"

    def close(self, t, limit_price=None, qty=None):
        return True, "closed"


jp.save_state = lambda st: None
jp.push = lambda t, s: PUSHES.append(t)
jp.ledger_row = lambda r: LEDGER.append(r)
jp.park_signal = lambda c, m, t, w: PARKS.append((m, w))
jp._TRADIER_INST = _Exec()
jp.spread_credit_mid = lambda u, e, l: None
jp._listed = lambda u, e, r, k: (True, True, None)
_SPOTS = {"SPX": 7700.0, "SPX500": 7700.0, "SPXW": 7700.0, "S&P500": 7700.0,
          "QQQ": 700.0, "SPY": 766.0, "IWM": 245.0, "NDX": 28800.0,
          "NDX100": 28800.0, "RUT": 2450.0, "EOG": 145.0}
jp._under_quote = lambda u: _SPOTS.get(str(u or "").upper(), 100.0)  # default: keep the chain moving
jp._expiry_close = lambda s, d: None
# the brain contributes nothing parseable: TEXT-first must carry the chain
jp.hermes_parse = lambda text, photo=None: {
    "instrument": None, "direction": "short", "action": "entry", "entry": None,
    "stop": None, "targets": [], "confidence": 50, "horizon": "",
    "rationale": "", "trim": None, "spread": None}


# ---- expectations from the audit's own verdicts -------------------------
def expectation(decision):
    d = (decision or "").upper()
    # genuine trade-deserving: plain misses and wrong-guard fixes
    if any(d.startswith(p) for p in ("MISSED, CAUSE", "MISSED WINNER", "P3 TRADED",
                                     "P2 FIXED", "P2 GUARD WRONG", "P2 GUARD/PARSER",
                                     "P2 INVALID", "FIXED TODAY", "HE LOST IT TOO")):
        return "TRADE"
    if d.strip() == "MISSED":
        return "TRADE"
    # fine NOT to trade: harmless misses, exits on unheld, levels, images
    if any(d.startswith(p) for p in ("MISSED, HARMLESS", "MISSED, NOT HELD",
                                     "MISSED, LEVEL", "MISSED, IMAGE",
                                     "P2 GUARD RIGHT", "P2 RISK_BLOCK", "P2 EXIT",
                                     "P2 DERIVATIVE", "P2 NARRATIVE", "P2 EMPTY",
                                     "P2 CONTEXT", "P2 IMAGE", "P4 NARR",
                                     "P4 EMPTY", "P4 LEVEL", "P4 PREDICTION",
                                     "P4 LINK", "P4 EXIT")):
        return "NO-TRADE"
    return "UNSCORED"


def main():
    verdicts = {}
    with open("spx_audit_verdicts.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            verdicts[str(r["msg"]).strip()] = r

    msgs = []
    with open("spx_window_raw.txt", encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 4 and parts[0].strip().isdigit():
                msgs.append((parts[0].strip(), parts[1].strip(), parts[3]))

    crashes = []
    score = {"HIT": 0, "OVERTRADE": 0, "MISS": 0, "OK-NOTRADE": 0,
             "UNSCORED": 0}
    overtrades, misses = [], []

    for mid, ts, text in msgs:
        PLACED.clear()
        PARKS.clear()
        LEDGER.clear()
        PUSHES.clear()
        # ERA CLOCK: anchor the date logic to the message's own day, else every
        # "for 10/29" reads as expired on today's clock and parks (false miss).
        try:
            _d = _dt.date.fromisoformat(ts[:10])
            jp.us_today = (lambda d: lambda: d)(_d)
            jp.us_session_for_today = (lambda d: lambda: d)(_d)
        except ValueError:
            pass
        try:
            jp._parse_trade(text or "", mid, None,
                            {"seen_msg": {}, "open": {}, "closed": {},
                             "alerts_today": 0}, 0)
        except Exception as e:
            crashes.append((mid, f"{type(e).__name__}: {e}"))
            traceback.print_exc(limit=1)
            continue
        system = "TRADE" if PLACED else ("PARK" if PARKS else "RECORD")
        exp = expectation(verdicts.get(mid, {}).get("decision", ""))
        if exp == "UNSCORED":
            score["UNSCORED"] += 1
        elif exp == "TRADE":
            if system == "TRADE":
                score["HIT"] += 1
            else:
                score["MISS"] += 1
                misses.append((mid, system, verdicts[mid]["decision"][:28],
                               (text or "")[:48]))
        else:  # NO-TRADE expected
            if system == "TRADE":
                score["OVERTRADE"] += 1
                overtrades.append((mid, verdicts[mid]["decision"][:28],
                                   (text or "")[:48]))
            else:
                score["OK-NOTRADE"] += 1

    n = len(msgs)
    print(f"=== E2E REHEARSAL — {n} real messages through the full chain ===")
    print(f"crashes        : {len(crashes)}  (must be 0)")
    print(f"trade-expected : {score['HIT']} converted / {score['MISS']} missed")
    print(f"no-trade expect: {score['OK-NOTRADE']} held / {score['OVERTRADE']} OVERTRADES")
    print(f"unscored rows  : {score['UNSCORED']} (plumbing/ambiguous audit labels)")
    print("caveat: gate spot checks use stub quotes; disposition CLASS is the score.")
    if overtrades:
        print("\nOVERTRADES (traded what must not trade):")
        for mid, dec, txt in overtrades[:15]:
            print(f"  {mid:<6} audit: {dec:<28} | {txt}")
    if misses:
        print("\nMISSED conversions (audit expected a trade):")
        for mid, sysd, dec, txt in misses[:15]:
            print(f"  {mid:<6} system: {sysd:<7} audit: {dec:<28} | {txt}")
    if crashes:
        print("\nCRASHES:")
        for mid, err in crashes:
            print(f"  {mid}: {err}")
    return 1 if crashes else 0


if __name__ == "__main__":
    sys.exit(main())
