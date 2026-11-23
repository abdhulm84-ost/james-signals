"""
reprocess.py — RE-RUN a slice of the channel through the FIXED pipeline logic,
safely. It exercises the real decision path (classify -> book -> risk -> route)
on the raw message texts, but:

  * hermes_parse is stubbed to a deterministic card (the fixes live in the
    POST-parse logic; the brain's non-determinism is not the thing under test).
  * _TRADIER is stubbed to a no-op executor (NO broker orders are placed).
  * push / ledger_row are captured to the console (no Telegram spam).
  * save_state is a no-op and we run on a FRESH state dict, so the live
    state.json is never touched. book.json IS written (that is the bookkeeping
    resolution we want to see).

Run:  python reprocess.py
"""
import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
import james_pipe as P

# Use a TEMPORARY book so the live book.json (with its accumulated history) is
# never disturbed by this demonstration run. Set REPROCESS_BOOK=live to target
# the real book.json instead (used for the one-time held-position backfill).
import os
if os.environ.get("REPROCESS_BOOK") == "live":
    P.BOOK_FILE = Path(__file__).parent / "book.json"
else:
    _TMP_BOOK = Path(__file__).parent / "book_reprocess_tmp.json"
    if _TMP_BOOK.exists():
        _TMP_BOOK.unlink()
    P.BOOK_FILE = _TMP_BOOK

# ---- capture side effects -------------------------------------------------
PUSHES = []
P.push = lambda text, st=None: (PUSHES.append(text), print("     [push]", text))
P.ledger_row = lambda row: LEDGER.append(row)
P.save_state = lambda st=None: None
LEDGER = []


class _Resp:
    ok = True
    id = "PAPER-NOOP"
    order_id = "PAPER-NOOP"
    status = "filled"
    price = 0.0

    def __getattr__(self, name):
        return None


class _NoExec:
    def place(self, rec):
        return _Resp()


P._TRADIER = lambda: _NoExec()

TICKERS = ["QCOM", "SMCI", "MCHP", "TXN", "IWM", "RDDT", "GD", "AN", "SPX", "NDX",
           "RUT", "RTY", "ES", "NQ", "GLW", "CVNA", "AMKR", "MELA"]


def fake_hermes(text, photo=None):
    """A deterministic, *correct* parse of the text — stands in for the brain so
    we exercise the fixed post-parse logic. Text is authoritative."""
    t = text or ""
    instr = next((x for x in TICKERS if re.search(rf"\b{x}\b", t)), None)
    legs = P.text_legs(t)
    strikes = P.text_strikes(t)
    low = t.lower()
    action = "entry" if P.entry_language(t) else (
        "exit" if re.search(r"book|take gains|close|exit|cut", low) else "entry")
    if "trim" in low:
        action = "entry"                      # the fixed gate classifies it
    direction = "short" if re.search(r"bear|put|short|drop|below", low) else "long"
    opt = None
    if len(legs) <= 1 and strikes and re.search(r"\b(call|put)s?\b|\d\s*[cp]\b", t, re.I):
        right = "put" if re.search(r"\bput|(\d)\s*p\b", low) else "call"
        opt = {"underlying": instr, "strike": strikes[0], "right": right}
    spread = {"type": "credit", "legs": legs} if len(legs) >= 2 else None
    trim = None
    m = re.search(r"(\d{1,3})\s*%", t)
    if m and "trim" in low:
        trim = int(m.group(1))
    return {"instrument": instr, "direction": direction, "action": action,
            "option": opt, "spread": spread, "entry": None, "stop": None,
            "targets": [], "trim": trim, "confidence": "", "horizon": ""}


P.hermes_parse = fake_hermes

MSGS = {
    "3643": "Triple Chop with QCOM in 1H at top and 10 points drop in 2 days, Tomorrow QCOM bear calls will make 100%",
    "3644": "$120 per contract, pl book gains",
    "3647": "AN went up 3% in Premarket post Earnings. Bull Puts will make nice gains at Open.",
    "3648": "QCOM close to 177.5 in Premarket, QCOM 177.5/182.5 Bear calls will make nearly 100% at Open.",
    "3649": "SMCI close to 32.4 in Premarket. SMCI 32.5/37.5 Bear calls will make nearly 100% at Open today, Pl trim 80% and hold 20% until expiry",
    "3650": "As discussed in Live yesterday, nearly all tickers which are reporting this morning before Open are Bullish, we took only AN and GD but all are moving good.",
    "3651": "MCHP is around $78 in Premarket, we will make nice gains at Open with 78/83 Bear calls",
    "3652": "TXN close to 306 in Premarket. TXN 305/310 Bear calls will make nice gains at Open",
    "3653": "RTY gave a Triple Chop in 1H so we can enter IWM Puts for Monday when market opens. We will see a nice drop with IWM/RUT",
    "3654": "Our Swing scanner gave nearly perfect numbers for the earnings for all tickers. Score above 60 good for Long and score below -60 good for Short",
    "3656": "Sell SPX 7645P n Buy 7635P n collect $455 to $460 for today",
}

st = {"seen_msg": {}, "open": {}}
now = time.time()
print("=" * 100)
print("REPROCESS 3643-3656 through the FIXED pipeline (no orders, live state untouched)")
print("=" * 100)
for mid in sorted(MSGS, key=int):
    LEDGER.clear()
    PUSHES.clear()
    print(f"\n--- msg {mid}: {MSGS[mid][:80]}")
    P.process_message(MSGS[mid], mid, now, None, st)
    for r in LEDGER:
        print(f"     [{r.get('event')}] {r.get('instrument') or ''} "
              f"{r.get('reason') or r.get('targets') or ''}")

print("\n" + "=" * 100)
print("BOOK STATE AFTER REPROCESS")
print("=" * 100)
book = P._load_book()
for p in book.positions.values():
    print(f"  {p.pid} {p.underlying:6} {p.structure:20} "
          f"{p.body if p.body is not None else '-':>7}/{p.wing_high if p.wing_high is not None else '-':<7} "
          f"state={p.state.value:8} qty_rem={p.qty_remaining}")
print("\nsummary:", book.summary())
