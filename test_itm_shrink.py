"""test_itm_shrink.py — bearish defined-risk guard hits shrink to 1 lot (D44).

User doctrine: risk rules shrink size / move exits, NEVER park flow. The
guard-refused bearish defined-risk band went 28W/5L in the parked outcomes
(msg 3533-type thin-cushion parks were the era's strike-drift parks) — so a
short CALL vertical hitting the ITM/cushion guard now goes out at 1 lot
instead of parking. The bullish side (short put vertical) lost money as a
band and keeps the park (watch-only, user 2026-11-13).
Replays the original parked messages. Run: python test_itm_shrink.py (exit 0)
"""
import datetime

import james_pipe as jp

FAILS = []
PLACED = []
PARKED = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


jp.save_state = lambda st: None
jp.push = lambda t, s: None
jp.ledger_row = lambda r: None
jp.park_signal = lambda c, m, t, w: PARKED.append(w)
_SPOT = {"v": 7718.0}          # per-case spot (930's real park ran at 7,545)
jp._under_quote = lambda u: _SPOT["v"] if str(u or "").upper() in ("SPX", "SPX500", "SPXW") else None
jp._listed = lambda u, e, r, k: (True, True, None)
jp.spread_credit_mid = lambda u, e, l: None


class _FO:
    def place(self, rec):
        PLACED.append(rec)

        class R:
            ok = True
            order_id = "T"
            venue = "tradier"
            reason = "s"
            fill_price = None
        return R()

    def order_fill(self, o):
        return "filled", 5.7

    def cancel(self, o):
        return True, "x"


jp._TRADIER_INST = _FO()
jp.us_today = lambda: datetime.date(2026, 10, 28)
jp.us_session_for_today = lambda: datetime.date(2026, 10, 28)


def run(txt, mid, card_qty=None):
    PLACED.clear()
    PARKED.clear()
    card = {"instrument": "SPX", "direction": "short", "action": "entry",
            "entry": None, "stop": None, "targets": [], "confidence": 70,
            "horizon": "", "rationale": "x", "trim": None, "spread": None}
    if card_qty is not None:
        card["qty"] = card_qty
    jp.hermes_parse = lambda text, photo=None, c=card: dict(c)
    st = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
    jp._parse_trade(txt, mid, None, st, 0)
    return st


# 1. the original parked message (3533): bearish thin-cushion -> 1 lot, SENT
#    spot 7718 vs short 7710C credit 5.70: break-even 7715.70 already passed.
st = run("Sell SPX 7710C n Buy 7720C n collect $570 for 10/29 - SPX completed", "3533")
check("bearish thin-cushion vertical is SENT (not parked)", PLACED and not PARKED,
      f"placed={len(PLACED)} parked={PARKED[:1]}")
check("and it goes out at exactly 1 lot",
      PLACED and PLACED[0].qty == 1 and all(l.qty == 1 for l in PLACED[0].legs),
      str([(r.qty, [l.qty for l in r.legs]) for r in PLACED]))

# 2. size stated in the text still caps at 1 on the shrink path
st = run("Sell SPX 7710C n Buy 7720C n collect $570 for 10/29, TO OPEN 5", "3533b")
check("text-stated qty 5 is overridden to 1 on the shrink path",
      PLACED and PLACED[0].qty == 1, str([r.qty for r in PLACED]))

# 3. the original parked message (930): BULLISH thin-cushion -> still parked
#    (its real park ran with spot 7,545: break-even 7,635.60 was 90 pt behind)
_SPOT["v"] = 7545.0
st = run("Sell SPX 7640P n Buy 7630P n collect $440 for 11/6", "930")
check("bullish thin-cushion vertical keeps the park (watch-only band)",
      not PLACED and any("thin cushion" in w for w in PARKED),
      f"placed={len(PLACED)} parked={PARKED[:1]}")

# 4. regression: an OTM bearish vertical still places at FULL qty
_SPOT["v"] = 7718.0
st = run("Sell SPX 7800C n Buy 7810C n collect $300 for 10/29", "ok1")
check("OTM bearish vertical unchanged: placed at text qty",
      PLACED and PLACED[0].qty >= 1 and not PARKED,
      f"qty={[r.qty for r in PLACED]} parked={PARKED[:1]}")

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
