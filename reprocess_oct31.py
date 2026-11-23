"""
reprocess_oct31.py — DRY-RUN of the 7 messages the pipeline under-captured on
Oct 30 (review_oct31.txt), through the FIXED pipeline logic, safely:

  * hermes_parse is stubbed to REPRODUCE THE BRAIN'S REAL FAILURE MODES (not a
    correct parse): 3672/3681/3683/3685/3688 came back action="none" with no
    spread; 3667 came back as a SINGLE-leg option card; 3669 as a normal
    single-leg card with expiry 2026-10-30 (which the old NZ-clock check
    rejected as "already past"). The thing under test is whether the new
    TEXT-FIRST routing rescues exactly these failures.
  * _TRADIER is stubbed (NO broker orders), tiger() is stubbed, push/ledger_row
    captured to console, save_state a no-op, book + parking lot go to TEMP files.
  * Live state.json / book.json / signals.csv / parking_lot.csv untouched.

Run:  python reprocess_oct31.py
"""
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
import james_pipe as P

# TEMP book + parking lot so live bookkeeping files are never touched
TMP_BOOK = Path(__file__).parent / "book_reprocess_oct31.json"
TMP_PARK = Path(__file__).parent / "park_reprocess_oct31.csv"
for f in (TMP_BOOK, TMP_PARK):
    if f.exists():
        f.unlink()
P.BOOK_FILE = TMP_BOOK
P.PKL = TMP_PARK

# ---- capture side effects -------------------------------------------------
PUSHES = []
LEDGER = []
RESULTS = {}
PLACED = []
_MID = ["0"]

P.push = lambda text, st=None: (
    PUSHES.append(text), print("     [push]", (text or "").replace("\n", " | ")))
P.ledger_row = lambda row: (
    LEDGER.append(row),
    print(f"     [{row.get('event')}] {(row.get('instrument') or '')} "
          f"{row.get('reason') or ''}"))
P.save_state = lambda st=None: None


class _Resp:
    ok = True
    id = "PAPER-NOOP"
    order_id = "PAPER-NOOP"
    status = "filled"

    def __getattr__(self, name):
        return None


class _NoExec:
    def place(self, rec):
        legs = [(str(getattr(l, "action", "?")).lower()[:1],
                 float(getattr(l, "strike", 0) or 0),
                 str(getattr(l, "right", "?")).lower()[:1])
                for l in (getattr(rec, "legs", None) or [])]
        PLACED.append({"msg": _MID[0], "underlying": getattr(rec, "underlying", None),
                       "credit": getattr(rec, "credit", None),
                       "expiry": str(getattr(rec, "expiry", None)), "legs": legs})
        print(f"     [tradier] msg {_MID[0]} place {getattr(rec, 'underlying', '?')} "
              f"credit={getattr(rec, 'credit', None)} "
              f"expiry={getattr(rec, 'expiry', '?')} :: {legs}")
        return _Resp()


P._TRADIER = lambda: _NoExec()


class _T:
    account = "PAPER"


P.tiger = lambda: (_T(), _T(), None)

# ---- the brain's REAL overnight cards (failure modes preserved) -----------
FLAT_NONE = {"instrument": None, "direction": "flat", "action": "none",
             "entry": None, "stop": None, "targets": [], "confidence": "",
             "horizon": "", "option": None, "spread": None, "trim": None,
             "rationale": "brain labelled none / no spread (real failure)"}

BRAIN = {
    # 3667: brain emitted ONE leg of the spread as a single-leg option card
    "3667": {"instrument": "QQQ", "direction": "short", "action": "entry",
             "entry": 0.91, "stop": None, "targets": [], "confidence": "",
             "horizon": "0dte", "trim": None,
             "option": {"underlying": "QQQ", "strike": 697, "right": "put",
                        "expiry": "2026-10-30"},
             "spread": None, "rationale": "single-leg card (real failure)"},
    # 3669: normal single-leg card; OLD code rejected expiry 2026-10-30 as past
    "3669": {"instrument": "XOM", "direction": "long", "action": "entry",
             "entry": 3.0, "stop": 1.5, "targets": [3.6, 4.6, 6.2],
             "confidence": "", "horizon": "0dte", "trim": None,
             "option": {"underlying": "XOM", "strike": 185, "right": "call",
                        "expiry": "2026-10-30"},
             "spread": None, "rationale": "single-leg card, expiry 10/30"},
    # the five spreads the brain labelled "none" with no spread field
    "3672": {**FLAT_NONE, "instrument": "SPX"},
    "3681": {**FLAT_NONE, "instrument": "SPX"},
    "3683": {**FLAT_NONE, "instrument": "SPX"},
    "3685": {**FLAT_NONE, "instrument": "SPY"},
    "3688": {**FLAT_NONE, "instrument": "SPY"},
}

P.hermes_parse = lambda text, photo=None: BRAIN[_MID[0]]

MSGS = {
    "3667": "Sell QQQ 697P n Buy 695P n collect $91 for today",
    "3669": "buy  XOM   185C at  3  for  10/30\nTargets $3.6/4.6/6.2\nSL 1.5\n\n-MK",
    "3672": "SPX sell 7620P n Buy 7610P n collect $410 for 11/2",
    "3681": "Sell SPX 7605/Buy 7695P n collect $350 to $355 for today, SPX bottom is 7695 for today",
    "3683": "Sell SPX 7610/Buy 7600P n collect $380 to $400 for today, SPX bottom is 7695 for today",
    "3685": "Sell SPY 761/Buy 759P n collect $154 to $150 for today, SPY bottom is 759 for today",
    "3688": "Sell SPY 761/Buy 759P n collect $118 to $120 for 11/2, SPY bottom is 759 for today - Don't miss trade**",
}

st = {"seen_msg": {}, "open": {}}
now = time.time()
print("=" * 100)
print("REPROCESS OCT-30 MISSES through the FIXED pipeline (no orders, live state untouched)")


def _get(name):
    try:
        return getattr(P, name)()
    except Exception as e:
        return f"n/a ({e})"


print(f"clock check: us_now = {_get('us_now')} | us_today = {_get('us_today')} "
      f"| 'for today' -> {_get('us_session_for_today')}")
print("=" * 100)
for mid in sorted(MSGS, key=int):
    _MID[0] = mid
    LEDGER.clear()
    PUSHES.clear()
    print(f"\n--- msg {mid}: {MSGS[mid][:80]!r}")
    P.process_message(MSGS[mid], mid, now, None, st)
    RESULTS[mid] = list(LEDGER)

# ---- verification ----------------------------------------------------------
print("\n" + "=" * 100)
print("VERIFICATION vs expected outcomes")
print("=" * 100)
fails = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        fails.append(label)


def place_for(mid):
    return [p for p in PLACED if p["msg"] == mid]


p = place_for("3667")
check(len(p) == 1, "3667: rescued from single-leg card -> 1 multileg placement")
if p:
    check(p[0]["underlying"] == "QQQ", "3667: underlying QQQ")
    check(sorted(p[0]["legs"]) == sorted([("s", 697.0, "p"), ("b", 695.0, "p")]),
          f"3667: legs sell 697P / buy 695P  (got {p[0]['legs']})")
    check(p[0]["credit"] is not None and abs(p[0]["credit"] - 0.91) < 1e-9,
          f"3667: credit 0.91 (got {p[0]['credit']})")

p = place_for("3669")
check(len(p) == 1 and p and p[0]["underlying"] == "XOM",
      "3669: XOM 185C placed (old code rejected expiry as past)")
if p:
    check(p[0]["legs"] == [("b", 185.0, "c")], f"3669: single buy leg (got {p[0]['legs']})")
    check(p[0]["expiry"] == "2026-10-30", f"3669: expiry kept 2026-10-30 (got {p[0]['expiry']})")

p = place_for("3672")
check(len(p) == 1 and p and p[0]["underlying"] == "SPXW",
      "3672: brain said 'none' -> text legs rescued it to SPXW multileg")
if p:
    check(sorted(p[0]["legs"]) == sorted([("s", 7620.0, "p"), ("b", 7610.0, "p")]),
          f"3672: legs sell 7620P / buy 7610P (got {p[0]['legs']})")
    check(p[0]["credit"] is not None and abs(p[0]["credit"] - 4.10) < 1e-9,
          f"3672: credit 4.10 (got {p[0]['credit']})")
    check(p[0]["expiry"] == "2026-11-02", f"3672: expiry 2026-11-02 (got {p[0]['expiry']})")

check(not place_for("3681")
      and any(r.get("event") == "PARKED" for r in RESULTS.get("3681", [])),
      "3681: inverted strikes (sell 7605/buy 7695) -> PARKED, no order")

p = place_for("3683")
check(len(p) == 1 and p and p[0]["underlying"] == "SPXW",
      "3683: 'for today' 0DTE-at-signal spread placed (was NO_TRADE)")
if p:
    check(sorted(p[0]["legs"]) == sorted([("s", 7610.0, "p"), ("b", 7600.0, "p")]),
          f"3683: legs sell 7610P / buy 7600P (got {p[0]['legs']})")
    check(p[0]["credit"] is not None and abs(p[0]["credit"] - 3.80) < 1e-9,
          f"3683: credit 3.80 (got {p[0]['credit']})")

p = place_for("3685")
check(len(p) == 1 and p and p[0]["underlying"] == "SPY",
      "3685: SPY 761/759P placed (was NO_TRADE)")
if p:
    check(sorted(p[0]["legs"]) == sorted([("s", 761.0, "p"), ("b", 759.0, "p")]),
          f"3685: legs sell 761P / buy 759P (got {p[0]['legs']})")
    check(p[0]["credit"] is not None and abs(p[0]["credit"] - 1.50) < 1e-9,
          f"3685: credit 1.50 lower bound (got {p[0]['credit']})")

p = place_for("3688")
check(len(p) == 1 and p and p[0]["underlying"] == "SPY",
      "3688: SPY 761/759P for 11/2 placed (was NO_TRADE)")
if p:
    check(sorted(p[0]["legs"]) == sorted([("s", 761.0, "p"), ("b", 759.0, "p")]),
          f"3688: legs sell 761P / buy 759P (got {p[0]['legs']})")
    check(p[0]["credit"] is not None and abs(p[0]["credit"] - 1.18) < 1e-9,
          f"3688: credit 1.18 (got {p[0]['credit']})")
    check(p[0]["expiry"] == "2026-11-02", f"3688: expiry 2026-11-02 (got {p[0]['expiry']})")

for mid in ("3667", "3669", "3672", "3683", "3685", "3688"):
    check(not any(r.get("event") == "PARKED" for r in RESULTS.get(mid, [])),
          f"{mid}: not parked")

print("\nstate 'open' keys after run:", sorted(st.get("open", {}).keys()))
if TMP_PARK.exists():
    print("park file rows:")
    for line in TMP_PARK.read_text(encoding="utf-8").splitlines():
        print("   ", line[:160])

print()
if fails:
    print(f"RESULT: {len(fails)} FAILURE(S)")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("RESULT: ALL CHECKS PASSED")
