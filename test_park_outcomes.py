"""test_park_outcomes.py — a parked trade is a lost outcome unless it settles.

User rule (2026-11-12): parking is fine, but every parked structure must be
captured in the ledger WITH its outcome — the sub is paid for, and the
would-have results are the raw material for the edge. This pins the settle
math against the real cases from the audit:
  - msg 3457: SPX 7735C/7745C bear call, collected 5.50, closed 7743.98
    -> (5.50 - 8.98) * 100 = -348.00 exactly what it would have lost
  - msg 3738: text legs 7590/7600 puts, collected 4.75, close below both
    -> the -525 that trading the text actually booked
  - 'collect $475' parked as dollars (475.0) must settle as 4.75
  - retro rows anchor 'for today' to THEIR ts, never the wall clock
Run: python test_park_outcomes.py   (exit 0 = green)
"""
import csv
import sys

import james_pipe as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


jp._PX_CACHE.clear()
PRICES = {}


def _fake_close(sym, day):
    return PRICES.get((str(sym).upper(), str(day)))


jp._expiry_close = _fake_close

# ---- settle math: 3457's real economics -------------------------------
# Sell 7735C / Buy 7745C, credit 5.50 -> closed 7743.98 on 2026-10-28
PRICES[("SPX", "2026-10-28")] = 7743.98
row = {"msg_id": "3457", "ts": "2026-10-28 12:57:19", "underlying": "SPX500",
       "structure": "vertical_credit", "body": "7735", "wing_low": "7745",
       "wing_high": "7745", "width": "10", "credit": "5.5",
       "raw": "Sell SPX 7735C n Buy 7745C n collect $550 for today - Swing"}
o = jp.park_outcome(row)
check("3457 settles at the real close", o and o["settle_px"] == 7743.98, str(o))
check("3457 would have lost -348.00",
      o and o["would_pnl"] == -348.0 and o["verdict"] == "would have lost", str(o))
check("3457 direction is bearish (short call at the lower strike)",
      o and o["direction"] == "bearish", str(o and o.get("direction")))
check("3457 expiry anchored to its OWN ts (for today = 10/28)",
      o and o["expiry"] == "2026-10-28", str(o and o.get("expiry")))

# ---- the -525 defect text (3738) --------------------------------------
PRICES[("SPX", "2026-11-03")] = 7550.0
row38 = {"msg_id": "3738", "ts": "2026-10-28 12:57:19", "underlying": "SPX500",
         "structure": "vertical_credit", "body": "7600", "wing_low": "7590",
         "wing_high": "7590", "width": "10", "credit": "4.75",
         "raw": "Sell 7600p n buy 7590p for 11/3 & collect $475"}
o = jp.park_outcome(row38)
check("3738 settles the TEXT legs at -525",
      o and o["would_pnl"] == -525.0 and o["verdict"] == "would have lost", str(o))

# ---- dollars-vs-points credit (3469 parked 475.0 for a 5-wide) --------
PRICES[("SPX", "2026-10-30")] = 7735.0
row69 = {"msg_id": "3469", "ts": "2026-10-28 12:57:19", "underlying": "SPX500",
         "structure": "short_iron_butterfly", "body": "7735", "wing_low": "7730",
         "wing_high": "7740", "width": "5", "credit": "475.0",
         "raw": "Iron fly in mean: Sell 7735C/P, Buy 7730P n 7740C collect $475 for 10/30"}
o = jp.park_outcome(row69)
check("475.0 credit normalises to 4.75",
      o and o["credit"] == 4.75, str(o and o.get("credit")))
check("fly at the body keeps the credit (475)",
      o and o["would_pnl"] == 475.0 and o["verdict"] == "WOULD HAVE WON", str(o))

# ---- decimal strikes (equity half-points parse) ----------------------
PRICES[("QCOM", "2026-11-06")] = 165.41
rowq = {"msg_id": "3472", "ts": "2026-11-01 12:57:19", "underlying": "QCOM",
        "structure": "vertical_credit", "body": "177.5", "wing_low": "182.5",
        "wing_high": "182.5", "width": "5", "credit": "2.58",
        "raw": "Sell QCOM 177.5C n Buy 182.5C n collect $258 for 11/6 - Swing"}
o = jp.park_outcome(rowq)
check("decimal strikes settle (177.5C)",
      o and o["expiry"] == "2026-11-06" and o["would_pnl"] == 258.0, str(o))
check("QCOM bear call is bearish", o and o["direction"] == "bearish", str(o))

# ---- future expiry waits for a later pass ----------------------------
# D40-family (D121-era catch): a HARDCODED future date ages into the past and
# the fixture silently stops testing what it was written to test. Roll it.
import datetime as _dt
_future = (_dt.date.today() + _dt.timedelta(days=16)).strftime("%m/%d")
rowf = {"msg_id": "998", "ts": "2026-11-12 06:23:39", "underlying": "EOG",
        "structure": "vertical_credit", "body": "36", "wing_low": "35",
        "wing_high": "35", "width": "1", "credit": "1.0",
        "raw": f"Sell EOG 36P n Buy 35P n collect $100 for {_future}"}
check("unexpired park waits (returns None)", jp.park_outcome(rowf) is None,
      f"expiry {_future} (rolled +16d)")

# ---- unparseable parks are CAPTURED, not dropped ---------------------
rowu = {"msg_id": "3903", "ts": "2026-10-28 12:57:19", "underlying": "SPX500",
        "structure": "multi_leg_unknown", "body": "7705", "wing_low": "7715",
        "wing_high": "7715", "width": "10", "credit": "",
        "raw": "Read this as Sell 7705 n buy 7715 for today"}
o = jp.park_outcome(rowu)
check("no-leg-rights park is recorded with a verdict",
      o is not None and o["verdict"].startswith("unsettleable"), str(o))

# ---- settle_parked: idempotent, appends only new rows ----------------
import io
import tempfile, os
tmp = tempfile.mkdtemp()
jp.PKL = jp.HERE / "parking_lot.csv"          # real register (read)
jp.PARK_OUT = jp.HERE / "park_outcomes.csv"    # real register (write)
sent = []
jp.push = lambda t, s: sent.append(t)
jp.log = lambda m: None
real_park = jp.PKL
# an isolated park lot with the two rows above
test_lot = jp.HERE / "parking_lot_test.csv"
with test_lot.open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(row.keys()))
    w.writeheader()
    w.writerow(row)
    w.writerow(row38)
out_file = jp.HERE / "park_outcomes_test.csv"
if out_file.exists():
    out_file.unlink()
jp.PKL, jp.PARK_OUT = test_lot, out_file
n1 = jp.settle_parked({})
n2 = jp.settle_parked({})
got = list(csv.DictReader(out_file.open(encoding="utf-8")))
check("settle_parked writes outcome rows", n1 == 2 and len(got) == 2, f"n1={n1} rows={len(got)}")
check("settle_parked is idempotent (re-run adds nothing)", n2 == 0, str(n2))
check("outcome rows carry the verdict and pnl",
      got and all(r["verdict"] and r["would_pnl"] != "" for r in got), str(got[:1]))
check("digest push fired once", len(sent) == 1 and "PARK OUTCOMES" in sent[0], str(sent))
jp.PKL, jp.PARK_OUT = real_park, jp.HERE / "park_outcomes.csv"
test_lot.unlink()
out_file.unlink()

# ---- the asof anchor must not move the live path ---------------------
check("live path unchanged (for today = current session)",
      jp._signal_expiry(None, "enter for today")
      == jp.us_session_for_today().isoformat())
check("next Friday default unchanged",
      jp._signal_expiry(None, "enter now") == jp.next_friday())

print(("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
raise SystemExit(0 if not FAILS else 1)
