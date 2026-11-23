"""test_cushion.py — break-even cushion at entry (4055: short 17 pts ITM).

OTM alone is not an edge: a short one point from the money is a 0DTE coin flip.
The guard requires the break-even to sit at least one credit away from spot.
D44 (shrink-not-park): a BEARISH vertical (short call) hitting the guard goes
out at 1 lot instead of parking; the BULLISH side (short put) keeps the park
(watch-only band — it lost money in the parked outcomes).
Run: python test_cushion.py   (exit 0 = green)
"""
import james_pipe as jp

FAILS = []
PARKS = []
PLACED = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


jp.save_state = lambda st: None
jp.push = lambda t, s: None
jp.ledger_row = lambda r: None
jp.park_signal = lambda card, mid, text, why: PARKS.append(why)
jp._listed = lambda u, e, r, k: (True, True, None)
jp.spread_credit_mid = lambda u, e, l: None   # text credits only in tests


class _F:
    def place(self, rec):
        PLACED.append(rec)

        class R:
            ok = True
            order_id = "T"
            venue = "tradier"
            reason = "stub"
            fill_price = None
        return R()


jp._TRADIER_INST = _F()

CALL = "Sell SPX 7415C n Buy 7425C n collect $455 to $460 for today"    # be 7419.55
PUT = "Sell SPX 7355P n Buy 7345P n collect $400 for today"             # be 7351.00


def run(text, spot, mid="700"):
    PARKS.clear()
    PLACED.clear()
    jp._under_quote = lambda u: spot
    norm = {"instrument": "SPXW", "direction": "short", "entry": None, "spread": None,
            "confidence": 60, "horizon": "day", "rationale": "x", "qty": 1}
    jp.open_multileg_tradier(norm, text, mid,
                             {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0})


# rule: the premium must cover any intrinsic — break-even cannot be behind spot
run(CALL, 7416.0, "801")      # short 7415C is 1 pt ITM, credit 4.55 covers it
check("call: 1 pt ITM covered by the credit trades", bool(PLACED) and not PARKS, str(PARKS))

run(CALL, 7420.0, "806")      # 5 pt ITM > 4.55 credit -> break-even behind spot
check("call: ITM beyond the credit SHRINKS to 1 lot and trades (D44)",
      bool(PLACED) and not PARKS and PLACED[0].qty == 1, f"{PARKS} qty={[r.qty for r in PLACED]}")

run(CALL, 7400.0, "802")
check("call: 19.5 pt cushion trades", bool(PLACED) and not PARKS,
      f"{len(PLACED)} placed {PARKS}")

run(PUT, 7352.0, "803")       # short 7355P is 3 pt ITM, credit 4.0 covers it
check("put: 3 pt ITM covered by the credit trades", bool(PLACED) and not PARKS, str(PARKS))

run(PUT, 7360.0, "804")
check("put: 9 pt cushion trades", bool(PLACED) and not PARKS,
      f"{len(PLACED)} placed {PARKS}")

run(CALL, 7432.0, "805")      # 17 pt ITM: deep, but bearish -> 1 lot (D44)
check("call deep ITM shrinks to 1 lot and trades (D44)",
      PLACED and PLACED[0].qty == 1 and not PARKS, str(PARKS))

run(PUT, 7320.0, "807")       # 35 pt ITM on the BULLISH side -> park
check("put deep ITM beyond the credit still parks (watch-only band)",
      PARKS and not PLACED and ("ITM" in PARKS[0] or "cushion" in PARKS[0]), str(PARKS))

print(("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
raise SystemExit(0 if not FAILS else 1)
