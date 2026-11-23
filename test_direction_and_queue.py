"""test_direction_and_queue.py — two fixes from the backlog P3 audit.

1. DIRECTION FROM GEOMETRY (msg 3284): a card-only spread with no credit word
   and no strikes in the text kept the brain's "long" label on a bull put
   (short-premium). Calls sell<buy and puts sell>buy are CREDITS -> short.
2. QUEUED WHILE SHUT: signals arrive around the clock including weekends. A
   resting entry must not burn its expiry timer while the venue is closed.
Run: python test_direction_and_queue.py   (exit 0 = green)
"""
import james_pipe as jp

FAILS = []
PLACED = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


jp.save_state = lambda st: None
jp.push = lambda t, s: None
jp.ledger_row = lambda r: None
jp.park_signal = lambda c, m, t, w: None
jp._listed = lambda *a: (True, False, None)
jp.spread_credit_mid = lambda u, e, l: None
jp._under_quote = lambda u: 7639.6
jp.bbc = lambda: type("B", (), {"get_quote": staticmethod(lambda i: 7639.6)})()


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

BULL_PUT = [{"action": "sell", "strike": 7640, "right": "put"},
            {"action": "buy", "strike": 7630, "right": "put"}]
BEAR_CALL = [{"action": "sell", "strike": 7415, "right": "call"},
             {"action": "buy", "strike": 7425, "right": "call"}]
CALL_DEBIT = [{"action": "buy", "strike": 7570, "right": "call"},
              {"action": "sell", "strike": 7580, "right": "call"}]


def run(text, brain_dir, legs):
    PLACED.clear()
    jp.hermes_parse = lambda t, photo=None: {
        "instrument": "SPX500", "direction": brain_dir, "action": "entry",
        "entry": None, "stop": None, "targets": [], "confidence": 50,
        "horizon": "", "rationale": "x", "trim": None,
        "spread": {"type": "credit", "legs": legs}, "option": None}
    jp.process_message(text, "x", 0, None,
                       {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0})
    return PLACED[0].direction if PLACED else None


# the exact 3284 wording: no verb, no strikes that are legs, brain says long
check("3284 text + bull put -> short",
      run("SPX moved over 7665+ and SNDK also moving up well, we should make gains with bot",
          "long", BULL_PUT) == "short")
check("narrative + bear call -> short",
      run("market looks heavy here", "long", BEAR_CALL) == "short")
check("narrative + call debit -> stays long",
      run("market looks heavy here", "long", CALL_DEBIT) == "long")
check("status message with real strikes still trades",
      run("We are holding 7640/7630 Bull Puts", "long", BULL_PUT) == "short")

# ---- queue while shut ----
t = {"id": "Q1", "instrument": "SPXW", "status": "pending", "opened": "2026-11-09 14:00:00",
     "order_id": "T1", "opt_id": "SPXW 261120P07500000", "qty": 1, "expiry": "2026-11-20"}
st = {"open": {"Q1": t}, "closed": {}, "alerts_today": 0, "seen_msg": {}}
jp._tradier_close_status = lambda oid: ("pending", None)
jp.market_open_now = lambda: False
jp.manage_once(st)
check("shut market keeps the entry open", "Q1" in st["open"], str(st["open"].keys()))
check("queued note set", "queued" in (t.get("fill_note") or ""), str(t.get("fill_note")))

# under the raised 6h threshold a 4.5h-old entry must still be alive when open
t45 = {"id": "K1", "instrument": "SPXW", "status": "pending",
       "opened": (jp.us_now().isoformat(timespec="seconds")), "order_id": "T45",
       "opt_id": "SPXW 261120P07500000", "qty": 1, "expiry": "2026-11-20"}
st45 = {"open": {"K1": t45}, "closed": {}, "alerts_today": 0, "seen_msg": {}}
jp.market_open_now = lambda: True
jp._tradier_close_status = lambda oid: ("pending", None)
jp.manage_once(st45)
check("4.5h under the 6h threshold stays alive", "K1" in st45["open"], str(st45["open"].keys()))

# BB deferred path also queues while shut
bbt = {"id": "B1", "instrument": "SPX500", "symbol": "SPX500", "status": "pending_open",
       "executor": "bb", "opened": "2026-11-09 14:00:00", "qty": 0.1,
       "stop": 1.0, "targets": [2.0], "direction": "long"}
bst = {"open": {"B1": bbt}, "closed": {}, "alerts_today": 0, "seen_msg": {}}
jp.market_open_now = lambda: False
jp.bbc = lambda: type("B", (), {"open_cfd": staticmethod(lambda *a, **k: {"ok": True, "deal": 1, "price": 1.0, "volume": 0.1}),
                                 "get_quote": staticmethod(lambda i: 7000.0)})()
jp.retry_open_bb(bbt, bst)
check("BB deferred open queues while shut",
      "B1" in bst["open"] and "queued" in (bbt.get("fill_note") or ""), str(bbt.get("fill_note")))

t2 = {"id": "E1", "instrument": "SPXW", "status": "pending", "opened": "2026-11-09 14:00:00",
      "order_id": "T2", "opt_id": "SPXW 261120P07500000", "qty": 1, "expiry": "2026-11-20"}
st2 = {"open": {"E1": t2}, "closed": {}, "alerts_today": 0, "seen_msg": {}}


class _T2:
    def cancel(self, oid):
        return True, "ok"


jp._TRADIER_INST = _T2()
jp.market_open_now = lambda: True
jp.manage_once(st2)
check("open market past the timer still expires", "E1" not in st2["open"], str(st2["open"].keys()))

print(("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
raise SystemExit(0 if not FAILS else 1)
