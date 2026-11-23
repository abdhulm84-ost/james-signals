"""
test_qty_resend.py — safest-qty resolution + duplicate re-send guard.

Covers the 2026-11-02 fixes from the message-by-message exercise:
  - resolve_qty: ambiguity -> 1 (min lot), screenshot qty authoritative,
    clamped to [1, max_qty] so a vision misread cannot inflate size
  - card qty passthrough through validate_card (it used to be dropped)
  - _legs_sig: parser dicts and book tuples identify the same structure
  - open_multileg_tradier: qty wires into the order/book; a re-send of a
    structure already OPEN is acked and never double-opened

All broker/state side effects are STUBBED. NEVER place a real order from a test.
Run:  python test_qty_resend.py
"""
import james_pipe as jp
import datetime as _dt

# D40 (clock family): pin the message expiry to the runtime clock — a
# hardcoded "for 11/21" went stale the moment the US date rolled to 11-22
# and every placement was refused as expired (13 FAILs, 2026-11-22).
_FUTD = jp.us_today() + _dt.timedelta(days=3)
FUT = _FUTD.isoformat()
FUT_TXT = f"{_FUTD.month}/{_FUTD.day}"

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


jp.save_state = lambda st: None
# D146: pinning the expiry to a real weekly date (D40) let the PREMIUM GUARD
# resolve a chain mid (0.02 for the deep-OTM 3-day leg) and "repair" the
# stated 3.20 away — the old Saturday 11/21 expiry had no chain, so the
# guard silently never ran. These checks assert the STATED-credit math:
# stub the mid so the guard stands down (live behaviour is covered by
# test_d134's CREDITFIX cases).
jp.spread_credit_mid = lambda *a, **k: None
_SPOTS = {"SPX": 7700.0, "SPXW": 7700.0, "SPX500": 7700.0, "NDX": 27000.0,
          "QQQ": 600.0, "MCHP": 78.0, "GLD": 370.0}
jp._under_quote = lambda u: _SPOTS.get(str(u or "").upper())
jp.push = lambda t, s: None
jp.ledger_row = lambda row: None
jp._load_book = lambda: jp.Book()
jp._save_book = lambda b: None

PLACED = []


class _FakeTradier:
    def place(self, rec):
        PLACED.append(rec)

        class R:
            ok = True
            order_id = "TESTORDER"
            venue = "tradier"
            reason = "stub"
            fill_price = None
        return R()

    def close(self, *a, **k):
        class R:
            ok = True
            order_id = "TESTCLOSE"
            venue = "tradier"
            reason = "stub"
            fill_price = None
        return R()


jp._TRADIER_INST = _FakeTradier()

# ---- resolve_qty: ambiguity -> 1, image qty authoritative, clamped ----
check("qty: no qty -> 1", jp.resolve_qty(None) == 1)
check("qty: missing keys -> 1", jp.resolve_qty(None, "", "x") == 1)
check("qty: image -6 -> 1 (D72 ceiling)", jp.resolve_qty(-6) == 1)
check("qty: string '6' -> 1 (D72 ceiling)", jp.resolve_qty("6") == 1)
check("qty: 0 -> 1", jp.resolve_qty(0) == 1)
check("qty: 3.7 -> 1 (D72 ceiling applies to orders)", jp.resolve_qty(3.7) == 1)
check("qty: 99 clamped to max_qty", jp.resolve_qty(99) == min(10, jp.CFG.get("max_qty", 10)))
check("qty: first valid wins, capped at 1", jp.resolve_qty(None, 4) == 1)

# ---- _legs_sig: dicts and book tuples identify the same structure ----
parser_legs = [{"action": "sell", "strike": 7745.0, "right": "call"},
               {"action": "buy", "strike": 7755.0, "right": "call"}]
book_legs = [(7745.0, "call", "sell"), (7755.0, "call", "buy")]
check("sig: parser dict == book tuple", jp._legs_sig(parser_legs) == jp._legs_sig(book_legs))
check("sig: order-insensitive",
      jp._legs_sig(parser_legs) == jp._legs_sig(list(reversed(parser_legs))))
check("sig: different structure differs",
      jp._legs_sig(parser_legs) != jp._legs_sig(
          [{"action": "sell", "strike": 7750.0, "right": "call"},
           {"action": "buy", "strike": 7760.0, "right": "call"}]))
check("sig: empty -> empty set", jp._legs_sig(None) == frozenset())

# ---- validate_card keeps qty (it used to be dropped at CARD_KEYS) ----
card = {"instrument": "SPX", "direction": "short", "action": "entry",
        "entry": 3.45, "qty": -6, "spread": {"type": "credit", "legs": parser_legs}}
ok, reason, norm = jp.validate_card(card, f"Sell SPX 7745C n Buy 7755C n collect $345 for {FUT_TXT}")
check("validate: ok", ok, reason)
check("validate: qty survives", norm.get("qty") == -6, f"got {norm.get('qty')!r}")

# ---- open_multileg_tradier: qty wires into the order + book ----
def fresh_st():
    return {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}


st = fresh_st()
norm = {"instrument": "SPX", "direction": "short", "entry": 3.45, "qty": 6,
        "spread": {"type": "credit", "legs": parser_legs},
        "confidence": 75, "horizon": "day", "rationale": "test spread"}
jp.open_multileg_tradier(dict(norm), f"Sell SPX 7745C n Buy 7755C n collect $345 for {FUT_TXT}",
                         "901", st)
check("multileg: one order placed", len(PLACED) == 1)
check("multileg: image qty 6 halves to 3, capped at 1 (D72)",
      PLACED and PLACED[0].qty == 1, f"got {PLACED[0].qty if PLACED else None}")
check("multileg: book qty 1", st["open"].get("TL901", {}).get("qty") == 1)
check("multileg: legs qty 1", PLACED and all(l.qty == 1 for l in PLACED[0].legs))

PLACED.clear()
st2 = fresh_st()
norm2 = dict(norm)
norm2.pop("qty")
jp.open_multileg_tradier(norm2, f"Sell SPX 7745C n Buy 7755C n collect $345 for {FUT_TXT}",
                         "902", st2)
check("multileg: ambiguous qty -> 1", PLACED and PLACED[0].qty == 1,
      f"got {PLACED[0].qty if PLACED else None}")

# ---- duplicate re-send: same structure already OPEN -> ack, no second order ----
PLACED.clear()
st3 = fresh_st()
st3["open"]["TL1"] = {"id": "TL1", "instrument": "SPXW", "direction": "short",
                      "status": "open_tradier", "qty": 1, "entry": 4.25,
                      "legs": book_legs}
jp.open_multileg_tradier(dict(norm), f"Sell SPX 7745C n Buy 7755C n collect $345 for {FUT_TXT}",
                         "903", st3)
check("resend: NO second order", len(PLACED) == 0)
check("resend: book unchanged (1 pos)", len(st3["open"]) == 1)
check("resend: recorded on the open pos",
      st3["open"]["TL1"].get("re_sends") and
      st3["open"]["TL1"]["re_sends"][0].get("msg_id") == "903")
check("resend: shadow SIM alias created",
      st3.get("shadow", {}).get("SIM903", {}).get("status") == "sh_open")
check("resend: shadow separate from book",
      "SIM903" not in st3["open"] and "SIM903" not in st3.get("closed", {}))

# ---- closed/closing position does NOT block a fresh re-entry ----
PLACED.clear()
st4 = fresh_st()
st4["open"]["TL2"] = {"id": "TL2", "instrument": "SPXW", "direction": "short",
                      "status": "close_pending", "qty": 1, "entry": 4.25,
                      "legs": book_legs}
jp.open_multileg_tradier(dict(norm), f"Sell SPX 7745C n Buy 7755C n collect $345 for {FUT_TXT}",
                         "904", st4)
check("re-entry after close_pending still opens", len(PLACED) == 1)

# ---- explicit add (option 1): scale into the open position ----
PLACED.clear()
st5 = fresh_st()
st5["open"]["TL3"] = {"id": "TL3", "instrument": "SPXW", "direction": "short",
                      "status": "open_tradier", "qty": 1, "entry": 4.25,
                      "expiry": FUT, "legs": book_legs}
norm5 = {k: v for k, v in norm.items() if k != "qty"}   # no image qty -> add 1
jp.open_multileg_tradier(norm5,
                         f"Sell SPX 7745C n Buy 7755C n collect $320 for {FUT_TXT} add more",
                         "905", st5)
check("add: order placed", len(PLACED) == 1)
check("add: qty merged 1+1=2", st5["open"]["TL3"]["qty"] == 2)
check("add: weighted entry (4.25+3.20)/2=3.725", st5["open"]["TL3"]["entry"] == 3.725,
      f"got {st5['open']['TL3']['entry']}")
check("add: recorded on pos", st5["open"]["TL3"].get("adds") and
      st5["open"]["TL3"]["adds"][0].get("msg_id") == "905")
check("add: no new position", "TL905" not in st5["open"])
check("add: NO shadow (it was traded, not acked)", not st5.get("shadow"))

# ---- "double size": add the size already held ----
PLACED.clear()
st6 = fresh_st()
st6["open"]["TL4"] = {"id": "TL4", "instrument": "SPXW", "direction": "short",
                      "status": "open_tradier", "qty": 3, "entry": 4.0,
                      "expiry": FUT, "legs": book_legs}
norm6 = {"instrument": "SPX", "direction": "short", "entry": 3.2,
         "spread": {"type": "credit", "legs": parser_legs},
         "confidence": 75, "horizon": "day", "rationale": "test add"}  # no qty
jp.open_multileg_tradier(norm6,
                         f"Sell SPX 7745C n Buy 7755C n collect $320 for {FUT_TXT} double size",
                         "906", st6)
check("double: adds held size (3)", PLACED and PLACED[0].qty == 3,
      f"got {PLACED[0].qty if PLACED else None}")
check("double: total 6", st6["open"]["TL4"]["qty"] == 6)

# ---- same legs, different STATED expiry, silent -> still acked (safety:
#      a wrong trade beats a missed one; the card flags it for the human) ----
PLACED.clear()
st7 = fresh_st()
st7["open"]["TL5"] = {"id": "TL5", "instrument": "SPXW", "direction": "short",
                      "status": "open_tradier", "qty": 1, "entry": 4.25,
                      "expiry": FUT, "legs": book_legs}
jp.open_multileg_tradier(dict(norm), "Sell SPX 7745C n Buy 7755C n collect $345 for 12/19",
                         "907", st7)
check("diff-expiry silent: acked not traded", len(PLACED) == 0 and
      st7["open"]["TL5"].get("re_sends"))
check("diff-expiry silent: no new position", "TL907" not in st7["open"])

# ---- unstated-expiry re-send (the next-Friday fallback trap) -> acked ----
PLACED.clear()
st8 = fresh_st()
st8["open"]["TL6"] = {"id": "TL6", "instrument": "SPXW", "direction": "short",
                      "status": "open_tradier", "qty": 1, "entry": 4.25,
                      "expiry": "2026-11-02", "legs": book_legs}
jp.open_multileg_tradier(dict(norm), "Sell SPX 7745C n Buy 7755C n collect $345",
                         "908", st8)
check("unstated-expiry re-send: acked", len(PLACED) == 0 and
      st8["open"]["TL6"].get("re_sends"))

# ---- shadow settlement (SIM alias): expiry PnL vs SPX, verdict ----
def _shadow_st(msg, exp="2026-11-01"):
    s = fresh_st()
    s["shadow"] = {f"SIM{msg}": {
        "id": f"SIM{msg}", "msg_id": msg, "instrument": "SPXW",
        "legs": [(7745.0, "call", "sell"), (7755.0, "call", "buy")],
        "credit": 3.45, "qty": 1, "expiry": exp, "status": "sh_open", "raw": ""}}
    return s


def _bb_quote(px):
    jp.bbc = lambda: type("B", (), {
        "get_quote": staticmethod(lambda inst: float(px))})()
    # the settle seam is _under_quote (D52) — stub it too, not just the feed
    jp._under_quote = lambda u: float(px)


_bb_quote(7708.0)                    # far below both strikes: full credit kept
st9 = _shadow_st("909")
jp.manage_shadow(st9)
sh = st9["shadow"]["SIM909"]
check("shadow: settled after expiry", sh["status"] == "sh_settled")
check("shadow: win +345 at SPX 7708", sh.get("pnl") == 345.0, f"got {sh.get('pnl')}")

_bb_quote(7750.0)                    # between strikes: partial loss
st10 = _shadow_st("910")
jp.manage_shadow(st10)
check("shadow: partial -155 at SPX 7750",
      st10["shadow"]["SIM910"].get("pnl") == -155.0,
      f"got {st10['shadow']['SIM910'].get('pnl')}")

_bb_quote(7760.0)                    # above both: max loss = width - credit
st11 = _shadow_st("911")
jp.manage_shadow(st11)
check("shadow: max loss -655 at SPX 7760",
      st11["shadow"]["SIM911"].get("pnl") == -655.0,
      f"got {st11['shadow']['SIM911'].get('pnl')}")

st12 = _shadow_st("912", exp="2099-01-01")   # not expired: untouched
jp.manage_shadow(st12)
check("shadow: not due -> still sh_open",
      st12["shadow"]["SIM912"]["status"] == "sh_open")

# ---- direction from economics: "collect" -> SHORT (PnL sign at close) ----
PLACED.clear()
stD = fresh_st()
normD = dict(norm)
normD["direction"] = "long"              # wrong brain label on purpose
jp.open_multileg_tradier(normD, f"Sell SPX 7745C n Buy 7755C n collect $345 for {FUT_TXT}",
                         "913", stD)
check("sign: collect spread books SHORT, not brain-long",
      PLACED and PLACED[0].direction == "short" and
      stD["open"]["TL913"]["direction"] == "short")

# ---- D51: shadow credit + honest credit-less verdicts ----
# a re-send whose text carries NO credit (4043: 'are nearly 100%') must borrow
# the matched position's entry so the expiry verdict is not a meaningless 0.00
PLACED.clear()
stC = fresh_st()
stC["open"]["TLC"] = {"id": "TLC", "instrument": "SPXW", "direction": "short",
                      "status": "open_tradier", "qty": 1, "entry": 4.25,
                      "legs": book_legs}
normC = dict(norm)
normC.pop("entry", None)
jp.open_multileg_tradier(normC, f"Sell SPX 7745C n Buy 7755C for {FUT_TXT}",
                         "914", stC)
check("resend: shadow borrows the open position's credit",
      stC.get("shadow", {}).get("SIM914", {}).get("credit") == 4.25,
      str(stC.get("shadow", {}).get("SIM914", {}).get("credit")))


def _shadow_nc(msg):
    s = fresh_st()
    s["shadow"] = {f"SIM{msg}": {
        "id": f"SIM{msg}", "msg_id": msg, "instrument": "SPXW",
        "legs": [(7745.0, "call", "sell"), (7755.0, "call", "buy")],
        "credit": None, "qty": 1, "expiry": "2026-11-01", "status": "sh_open",
        "raw": ""}}
    return s


_bb_quote(7708.0)                    # both legs OTM: expired worthless
stN = _shadow_nc("915")
jp.manage_shadow(stN)
check("credit-less shadow: expired worthless is a would-have winner",
      "expired worthless" in str(stN["shadow"]["SIM915"].get("verdict") or "")
      or "expired worthless" in str(stN["shadow"]["SIM915"].get("fill_note") or "")
      or "expired worthless" in str(stN["shadow"]["SIM915"]),
      str(stN["shadow"]["SIM915"]))
_bb_quote(7760.0)                    # short strike ITM: finished against
stN2 = _shadow_nc("916")
jp.manage_shadow(stN2)
check("credit-less shadow: ITM finish is called out",
      "ITM" in str(stN2["shadow"]["SIM916"]),
      str(stN2["shadow"]["SIM916"]))

# ---- D52: a shadow settles at ITS OWN underlying, not always SPX ----
_reqs = []
jp._under_quote = lambda u: (_reqs.append(str(u)), 700.0)[1]
stQ = fresh_st()
stQ["shadow"] = {"SIM917": {
    "id": "SIM917", "msg_id": "917", "instrument": "QQQ",
    "legs": [(656.0, "put", "sell"), (654.0, "put", "buy")],
    "credit": 0.6, "qty": 1, "expiry": "2026-11-01", "status": "sh_open", "raw": ""}}
jp.manage_shadow(stQ)
check("D52: QQQ shadow settles at the QQQ print, not SPX",
      any("QQQ" in r for r in _reqs) and stQ["shadow"]["SIM917"]["settle_px"] == 700.0,
      f"reqs={_reqs} px={stQ['shadow']['SIM917'].get('settle_px')}")

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    raise SystemExit(1)
print("ALL PASS")
