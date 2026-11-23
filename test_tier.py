"""
test_tier.py — risk-tier stamping + per-tier digest stats (promotion ladder).
tier_of is pure; the spread cases run through the real multileg path with stubs.
Run: python test_tier.py   (exit 0 = green)
"""
import datetime
import james_pipe as jp

FAILS = []
PUSHED = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


# ---- 1. pure classification ----------------------------------------------
check("OTM high-quality 3dte spread -> bankable",
      jp.tier_of(defined=True, legs_n=2, short=True, lotto=False,
                 quality=0.62, dte=3, itm=False) == "bankable")
check("thin credit 0.40 -> middle",
      jp.tier_of(defined=True, legs_n=2, short=True, lotto=False,
                 quality=0.40, dte=3, itm=False) == "middle")
check("same-day (0DTE) spread -> middle",
      jp.tier_of(defined=True, legs_n=2, short=True, lotto=False,
                 quality=0.80, dte=0, itm=False) == "middle")
check("already ITM -> high even with fat credit",
      jp.tier_of(defined=True, legs_n=2, short=True, lotto=False,
                 quality=0.90, dte=5, itm=True) == "high")
check("lotto -> high", jp.tier_of(defined=True, legs_n=4, short=True,
                                  lotto=True, quality=0.9, dte=4, itm=False) == "high")
check("naked short put -> high", jp.tier_of(defined=False, legs_n=1, short=True,
                                            lotto=False, quality=None, dte=5, itm=False) == "high")
check("long single option -> middle", jp.tier_of(defined=False, legs_n=1, short=False,
                                                 lotto=False, quality=None, dte=5, itm=False) == "middle")

# ---- 2. stamping through the real path -----------------------------------
jp.save_state = lambda st: None
jp.ledger_row = lambda row: None
jp._listed = lambda u, e, r, k: (True, True, None)
SPOT = {"SPX": 400.0, "SPXW": 400.0}
jp._under_quote = lambda u: SPOT.get(u)
PLACED = []


class _FT:
    def place(self, rec):
        PLACED.append(rec)

        class R:
            ok = True
            order_id = "T1"
            venue = "tradier"
            reason = "stub"
            fill_price = None
        return R()

    def close(self, t, limit_price=None, qty=None):
        return True, "cl"


jp._TRADIER_INST = _FT()
jp._tradier_close_status = lambda oid: ("filled", 6.0)
jp.push = lambda t, s: PUSHED.append(t)

_fd = datetime.date.today() + datetime.timedelta(days=3)
FUT = f"{_fd.month}/{_fd.day}"


def _st():
    return {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}


card = {"instrument": "SPX", "direction": "short", "entry": 6.0,
        "spread": {"type": "credit", "legs": [
            {"action": "sell", "strike": 390.0, "right": "put"},
            {"action": "buy", "strike": 380.0, "right": "put"}]},
        "confidence": 60, "horizon": "swing", "rationale": "x"}
st = _st()
jp.open_multileg_tradier(card, f"Sell SPX 390P n Buy 380P n collect $600 for {FUT}",
                         "960", st)
t = next(iter(st["open"].values()), {})
check("OTM spread stamped BANKABLE", t.get("tier") == "bankable", str(t.get("tier")))
check("card carries the badge",
      any("BANKABLE" in p for p in PUSHED), str(PUSHED[:1]))

PLACED.clear()
PUSHED.clear()
SPOT["SPX"] = SPOT["SPXW"] = 380.0   # short put 390 now ITM
st = _st()
jp.open_multileg_tradier(card, f"Sell SPX 390P n Buy 380P n collect $600 for {FUT}",
                         "961", st)
t = next(iter(st["open"].values()), {})
check("ITM spread stamped HIGH", t.get("tier") == "high", str(t.get("tier")))

# ---- 3. digest prints per-tier lines ------------------------------------
PUSHED.clear()
day = jp.today()
st = _st()
st["closed"] = {
    "A": {"id": "A", "tier": "bankable", "pnl": 120.0, "exit_ts": day + " 10:00"},
    "B": {"id": "B", "tier": "bankable", "pnl": -80.0, "exit_ts": day + " 11:00"},
    "C": {"id": "C", "tier": "high", "pnl": -200.0, "exit_ts": day + " 12:00"},
}
jp.daily_digest(st)
blob = "\n".join(PUSHED)
check("digest shows a bankable line with expectancy",
      "BANKABLE" in blob and "exp $+20.00/trade" in blob, blob)
check("digest shows the high-tier line", "HIGH" in blob and "$-200.00" in blob, blob)

print(f"\n{len(FAILS)} FAIL(S)" if FAILS else "\nALL PASS")
raise SystemExit(1 if FAILS else 0)
