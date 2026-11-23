"""test_text_image_strikes.py — the filled-order TICKET outranks the text.

msg 3738: the text said "Sell 7600p n buy 7590p", the filled-order ticket in the
image said 7600/7610. The pipeline traded the TEXT — a different spread, 10
points away, booked at -525. The image is what the broker actually filled.
Park on a disagreement rather than trade a structure nobody ordered.
Run: python test_text_image_strikes.py   (exit 0 = green)
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
jp.park_signal = lambda c, m, t, w: PARKS.append(w)
jp._listed = lambda *a: (True, False, None)
jp.spread_credit_mid = lambda u, e, l: None
jp._under_quote = lambda u: 7600.0
jp.bbc = lambda: type("B", (), {"get_quote": staticmethod(lambda i: 7600.0)})()


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

TXT = "Sell 7600p n buy 7590p for 11/3 & collect $475"


def run(img, credit=None, img_qty=None, qty=7, txt=TXT):
    PARKS.clear()
    PLACED.clear()
    norm = {"instrument": "SPXW", "direction": "short",
            "entry": credit if credit is not None else 4.75,
            "spread": None, "confidence": 50, "horizon": "", "rationale": "x",
            "qty": qty, "img_strikes": img, "img_qty": img_qty}
    if credit is not None:
        norm["img_credit"] = credit
    jp.open_multileg_tradier(norm, txt, "3738",
                             {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0})
    return PARKS, [(l.strike, l.action) for l in PLACED[0].legs] if PLACED else None


p, legs = run([7600.0, 7610.0])
check("text vs ticket disagree -> parked", bool(p) and not PLACED, str(p))
check("the park names both structures",
      p and "7590" in p[0] and "7610" in p[0], str(p))

p, legs = run([7590.0, 7600.0])
check("matching ticket passes through", not (p and "disagree" in p[0]),
      str(p))

p, legs = run(None)
check("no image at all passes through", not (p and "disagree" in p[0]), str(p))

# a PARTIAL ticket (image shows one leg of the spread) is not a conflict
p, legs = run([7600.0])
check("partial ticket passes through (image is a subset)",
      not (p and "disagree" in p[0]), str(p))

# an iron-fly ticket with MORE legs than the text named is not a conflict
p, legs = run([7590.0, 7600.0, 7580.0])
check("ticket with extra legs passes (text is a subset)",
      not (p and "disagree" in p[0]), str(p))

# ---- the full field gate: strikes AND credit AND qty (3843 disagreed on all) ----
p, legs = run([7600.0, 7610.0], credit=4.75, img_qty=7, qty=7)
check("3738: strikes disagree -> parked", bool(p) and not PLACED, str(p))

T2 = "Sell spx 7470p n buy 7460p n collect $380"
p, legs = run([7460.0, 7450.0], credit=4.1, img_qty=6, qty=1, txt=T2)
check("3843: strikes+credit+qty disagree -> parked", bool(p) and not PLACED, str(p))

p, legs = run([7460.0, 7470.0], credit=6.0, img_qty=1, qty=1, txt=T2)
check("credit alone disagreeing parks", bool(p) and "credit" in p[0], str(p))

# D75: trading BELOW the ticket's size is the risk ceiling, not a conflict.
p, legs = run([7460.0, 7470.0], credit=3.8, img_qty=6, qty=1, txt=T2)
check("qty below the ticket does NOT park (risk cap, D75)",
      not (p and "qty" in str(p)), str(p))
# D77: the guard now compares the REQUESTED size (pre-ceiling) against the
# ticket, so trading MORE than the ticket is detectable again and must park.
p, legs = run([7460.0, 7470.0], credit=3.8, img_qty=1, qty=6, txt=T2)
check("qty ABOVE the ticket parks (over-sized vs the ticket, D77)",
      bool(p) and "exceeds" in str(p[0]), str(p))

p, legs = run([7460.0, 7470.0], credit=3.8, img_qty=None, qty=1, txt=T2)
check("no image qty -> no qty conflict", not (p and "qty" in p[0]), str(p))

p, legs = run([7460.0, 7470.0], credit=3.8, img_qty=1, qty=1, txt=T2)
check("all fields agree -> not parked on conflict",
      not (p and "disagree" in p[0]), str(p))

print(("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
raise SystemExit(0 if not FAILS else 1)
