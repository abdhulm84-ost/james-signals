"""test_credit_guard.py — a screenshot-read premium must be reconciled.

The credit anchors sizing AND P&L. Vision misread one at 3.45 against a true
5.70 (msg 3956). Rule: if the chain can be quoted, the mid wins beyond 25%;
with no quote the only check left is structural, so an impossible credit parks
and a merely unverified one trades a single contract.
Run: python test_credit_guard.py   (exit 0 = green)
"""
import james_pipe as jp

FAILS = []
PLACED = []
PARKS = []
ROWS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


jp.save_state = lambda st: None
jp.push = lambda t, s: None
jp.ledger_row = lambda r: ROWS.append(r.get("event"))
jp.park_signal = lambda c, m, t, w: PARKS.append(w)
jp._listed = lambda *a: (True, True, None)
jp._under_quote = lambda u: 700.0
jp.bbc = lambda: type("B", (), {"get_quote": staticmethod(lambda i: 700.0)})()


class _F:
    def place(self, rec):
        PLACED.append((rec.credit, rec.qty))

        class R:
            ok = True
            order_id = "T"
            venue = "tradier"
            reason = "stub"
            fill_price = None
        return R()


jp._TRADIER_INST = _F()
LEGS = [{"action": "sell", "strike": 693.0, "right": "put"},
        {"action": "buy", "strike": 691.0, "right": "put"}]
def txt_for(credit):
    return ("Sell QQQ 693P n Buy 691P n collect $%g for today" % (credit * 100))


def run(credit, mid, qty=4):
    PLACED.clear()
    PARKS.clear()
    ROWS.clear()
    jp.spread_credit_mid = (lambda u, e, l: mid) if mid is not None else (lambda u, e, l: None)
    jp.open_multileg_tradier(
        {"instrument": "QQQ", "direction": "short", "entry": credit,
         "spread": {"type": "credit", "legs": LEGS}, "confidence": 50,
         "horizon": "day", "rationale": "x", "qty": qty,
         "credit_from_image": True},
        txt_for(credit), "1", {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0})


run(0.30, 1.01)
check("a misread credit is repaired to the mid",
      PLACED and PLACED[0][0] == 1.01, str(PLACED))
check("the repair is recorded", "CREDITFIX" in ROWS, str(ROWS))

run(1.01, 1.01)
check("agreement passes through untouched",
      PLACED and PLACED[0][0] == 1.01, str(PLACED))

run(4.00, None)
check("impossible credit with no chain parks",
      PARKS and not PLACED and "impossible" in PARKS[0], str(PARKS))

run(1.01, None)
check("unverified credit trades one contract", PLACED and PLACED[0][1] == 1,
      str(PLACED))

print(("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
raise SystemExit(0 if not FAILS else 1)
