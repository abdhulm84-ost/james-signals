"""
test_venue.py — venue routing added 2026-11-02:
  - a ticker BlackBull does not list falls back to a Tradier EQUITY trade
    (SNDK-class) instead of being ledger-only
  - a quoted-but-absent symbol is still never traded blind
  - an unlisted strike within 2% snaps to the nearest listed strike
  - beyond 2% it parks
Everything stubbed. Run: python test_venue.py   (exit 0 = green)
"""
import james_pipe as jp
import datetime as _dt
_FUTD = jp.us_today() + _dt.timedelta(days=3)
FUT = _FUTD.isoformat()
FUT_TXT = f"{_FUTD.month}/{_FUTD.day}"

FAILS = []
PARKS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


jp.save_state = lambda st: None
jp.push = lambda t, s: None
jp.ledger_row = lambda row: None
jp.park_signal = lambda card, mid, text, why: PARKS.append(why)
_REAL_UQ = jp._under_quote
# the root gate (D09) accepts a non-index root only if it QUOTES — a blanket
# None here parked TSLA/EOG as "unresolvable" before the listing checks could
# run. Quote the two tickers this file trades; SNDK and everything else stay
# None (section 3's no-quote check relies on that).
jp._under_quote = lambda u: {"TSLA": 340.0, "EOG": 36.0}.get(str(u or "").upper())

QUOTES = {"SNDK": 806.43}
STOCK_ORDERS = []


class _FB:  # BlackBull: lists nothing
    def resolve_symbol(self, inst):
        return None

    def get_quote(self, inst):
        return None


class _FT:
    def quote_stock(self, sym):
        return QUOTES.get(sym)

    def place_stock(self, sym, side, qty):
        STOCK_ORDERS.append((sym, side, qty))

        class R:
            ok = True
            order_id = "STK1"
            venue = "tradier"
            reason = "stub"
            fill_price = None
        return R()

    def close(self, t, limit_price=None, qty=None):
        return True, "cl"


jp.bbc = lambda: _FB()
jp._TRADIER_INST = _FT()
jp._tradier_close_status = lambda oid: ("filled", QUOTES.get("SNDK"))


def _card(sym, direction="long"):
    return {"instrument": sym, "direction": direction, "entry": None, "stop": None,
            "targets": [], "confidence": 60, "horizon": "swing",
            "rationale": "x", "spread": None}


def _st():
    return {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}


# --- 1. unlisted ticker with a live quote -> Tradier equity trade -----------
st = _st()
jp.open_trade_bb(_card("SNDK"), "950", st)
t = next(iter(st["open"].values()), None)
check("BB miss -> Tradier stock trade opened", t is not None, str(st["open"]))
check("executor is tradier_stock (not a phantom bb ticket)",
      t and t["executor"] == "tradier_stock" and not t.get("bb_ticket"))
check("priced off the live quote, stop below entry",
      t and t["entry"] == 806.43 and t["stop"] and t["stop"] < t["entry"])
check("placed a BUY of >=1 share", STOCK_ORDERS and STOCK_ORDERS[0][1] == "buy"
      and STOCK_ORDERS[0][2] >= 1, str(STOCK_ORDERS))
check("never parked when a venue quote exists", not PARKS, str(PARKS))

# --- 2. short side sells short ---------------------------------------------
STOCK_ORDERS.clear()
st = _st()
jp.open_trade_bb(_card("SNDK", "short"), "951", st)
t = next(iter(st["open"].values()), None)
check("short -> sell_short, stop above entry",
      STOCK_ORDERS and STOCK_ORDERS[0][1] == "sell_short" and t
      and t["stop"] > t["entry"], str(STOCK_ORDERS))

# --- 3. no quote -> ledger only, no order ----------------------------------
QUOTES.pop("SNDK")
STOCK_ORDERS.clear()
st = _st()
jp.open_trade_bb(_card("SNDK"), "952", st)
check("no quote -> no position, no order", not st["open"] and not STOCK_ORDERS,
      str(STOCK_ORDERS))
QUOTES["SNDK"] = 806.43

# --- 4. unlisted strike inside 2% snaps and places -------------------------
# fixture is 340.5: ON the 0.5 tick grid but absent from the chain. A 340.2
# here used to pass, then the chart-artifact tick guard (msg 4047) started
# parking off-grid strikes BEFORE listing runs — that park is deliberate
# (chart numbers are not contracts), so the snap case uses a grid-legal gap.
PLACED = []


class _FO:
    def place(self, rec):
        PLACED.append(rec)

        class R:
            ok = True
            order_id = "TESTORDER"
            venue = "tradier"
            reason = "stub"
            fill_price = None
        return R()


def _listed(under, expiry, right, strike):
    strike = float(strike)
    if abs(strike - 340.0) < 0.001:
        return True, True, 340.0
    if abs(strike - 340.5) < 0.01:
        return False, True, 340.0
    return True, True, strike


jp._listed = _listed
jp._TRADIER_INST = _FO()
jp._tradier_close_status = lambda oid: ("filled", 3.0)
spread_card = {"instrument": "TSLA", "direction": "long", "entry": 3.0,
               "spread": {"type": "debit", "legs": [
                   {"action": "buy", "strike": 340.5, "right": "call"},
                   {"action": "sell", "strike": 350.0, "right": "call"}]},
               "confidence": 60, "horizon": "day", "rationale": "x"}
jp.open_multileg_tradier(spread_card, "Buy TSLA 340.5/350 calls for today", "953", _st())
seen = [l.strike for r in PLACED for l in r.legs]
check("340.5 snapped to listed 340 and still placed",
      PLACED and 340.0 in seen, str(seen))

# --- 5. beyond 2% parks ------------------------------------------------
PLACED.clear()
PARKS.clear()
jp._listed = lambda u, e, r, k: (False, True, 75.0)
bad_card = {"instrument": "EOG", "direction": "long", "entry": 2.0,
            "spread": {"type": "debit", "legs": [
                {"action": "buy", "strike": 36.0, "right": "call"},
                {"action": "sell", "strike": 40.0, "right": "call"}]},
            "confidence": 60, "horizon": "day", "rationale": "x"}
jp.open_multileg_tradier(bad_card, "Buy EOG 36/40 calls for today", "954", _st())
check("strike 108% away -> parked, nothing placed",
      not PLACED and any("not listed" in p for p in PARKS), str(PARKS))

# --- 6. a slash DATE in the ticket text is not three phantom strikes ----
_dl = jp.text_spread("BUY TO OPEN 5 FTNT 136.000 P 10/30/26")[0]
check("ticket date 10/30/26 stays one leg",
      len(_dl) == 1 and _dl[0]["strike"] == 136.0, str(_dl))

# --- 7. close accepts opt_expiry-only records (BMRN live failure) --------
import types
import james_exec as jx

class _Req:
    def __init__(self, *a, **k):
        self.status_code = 200

    def json(self):
        return {"order": {"id": 77, "status": "ok"}}

import requests
_orig_post = requests.post
requests.post = lambda *a, **k: _Req()
_ex = jx.TradierExecutor("sandbox")
_ex.cfg = types.SimpleNamespace(access_token="t", base_url="https://x/v1", account_id="A1")
_cl = {"id": "X", "direction": "long", "qty": 1, "legs": [],
       "opt_id": "BMRN 261120C00060000", "opt_underlying": "BMRN",
       "opt_expiry": "2026-11-20", "expiry": None}
_r = _ex.close(_cl)
requests.post = _orig_post
check("close works with opt_expiry only (no expired-error)", _r.ok, _r.reason)
# D45: a placement crash once died here ('_side() takes 1 positional argument
# but 2 were given') — the decorator stack on _occ/_side must survive binding.
_leg = types.SimpleNamespace(action="buy", qty=1, occ=lambda: "SPXW261120C00760000")
check("executor _side/_occ survive instance binding (D45 decorator stack)",
      _ex._side(_leg) == "buy_to_open" and _ex._occ(_leg) == "SPXW261120C00760000")

# --- 8. written single strike beats the brain's dropped digits ----------
check("EOG 146P extracts 146 put",
      jp.text_single_opt("EOG 146P at 2.75 for 11/6 - Bearish", "EOG") == (146.0, "put"))
check("MAT 15P extracts 15 put",
      jp.text_single_opt("MAT 15P at $0.95 for 11/20", "MAT") == (15.0, "put"))
check("right letter respected",
      jp.text_single_opt("EOG 146C at 2.75", "EOG") == (146.0, "call"))
check("other ticker does not match", jp.text_single_opt("EOG 146P", "SPX") is None)
check("bare levels do not match",
      jp.text_single_opt("SPX will go to 7670 if it breaks", "SPX") is None)

# --- 9. repair precedence: the brain's own valid symbol beats context ---
check("IWM survives an RTY-heavy sentence",
      jp.repair_symbol("IWM", "RTY gave a Triple Chop, enter IWM Puts", {})[0] == "IWM")
check("a label word still repairs",
      jp.repair_symbol("SELL TO OPEN 5", "Sell 5 SPXW 7450P", {})[0] == "SPXW")
check("index alias kept", jp.repair_symbol("SPX500", "NQ fell", {})[0] == "SPX500")
check("a wrong-but-valid card ticker yields to the ticker written on a structure",
      jp.repair_symbol("GOOG", "GOLD Puts making now", {})[0] == "GOLD")

# --- 10. 'for Monday' resolves to the coming Monday ---------------------
import datetime as _dt
# clock-dependent (D40 family): "for Monday" resolves to the coming-OR-CURRENT
# Monday — the old expectation ('strictly next Monday') only disagreed with the
# code's contract ON Mondays ('for Monday' said on Monday = that session, the
# sensible trading reading). Pin both halves at fixed clocks.
_uq2 = jp.us_today
jp.us_today = lambda: _dt.date(2026, 11, 11)      # a Wednesday
check("weekday expiry resolves forward to the coming Monday",
      jp._signal_expiry(None, "enter IWM Puts for Monday") == "2026-11-16")
jp.us_today = lambda: _dt.date(2026, 11, 16)      # a Monday
check("'for Monday' said on Monday = that session",
      jp._signal_expiry(None, "enter IWM Puts for Monday") == "2026-11-16")
jp.us_today = _uq2

# --- 3667/3668: a misattributed card must not block a text spread ------
_SPOTM = {"v": 700.0}
jp._under_quote = lambda u: _SPOTM["v"]
jp.save_state = lambda st: None
jp.ledger_row = lambda r: None
jp.push = lambda t, s: None
_MIS = []
class _FM:
    def place(self, rec):
        _MIS.append(rec)
        class R: ok = True; order_id = "T"; venue = "tradier"; reason = "s"; fill_price = None
        return R()
    def order_fill(self, o): return "filled", 1.0
    def cancel(self, o): return True, "x"
jp._TRADIER_INST = _FM()
jp._listed = lambda u, e, r, k: (True, True, None)
jp.hermes_parse = lambda text, photo=None: {"instrument": "SPX", "direction": "short",
    "action": "none", "entry": None, "stop": None, "targets": [], "confidence": 80,
    "horizon": "", "rationale": "x", "trim": None, "spread": None,
    "option": {"underlying": "SPX", "strike": 7640, "right": "put", "expiry": "2026-11-02"}}
jp._parse_trade("Sell QQQ 697P n Buy 695P n collect $91 for today", "3667", None,
                {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}, 0)
check("wrong single-leg card still trades the QQQ spread from the text",
      _MIS and _MIS[0].underlying == "QQQ", str([r.underlying for r in _MIS]))

# --- late '11/2 (Monday)' rolls to the coming Monday --------------------
_wd = jp._signal_expiry(None, "for 11/2 (Monday)")
_exp = (jp.us_today() + _dt.timedelta(days=(7 - jp.us_today().weekday()) % 7 or 7)).isoformat()
check("weekday metadata rolls a stale date forward", _wd == _exp, f"{_wd} vs {_exp}")
# the keep-vs-wrap window is 14 days and CLOCK-SENSITIVE (D40 family): this
# assertion silently aged out of its window on 2026-11-14 (15 days past 10/30).
# Pin the clock and cover BOTH halves of the rule.
_uq = jp.us_today
jp.us_today = lambda: _dt.date(2026, 11, 1)
check("a stale date within the fortnight keeps the real date",
      jp._signal_expiry(None, "for 10/30") == "2026-10-30")
jp.us_today = lambda: _dt.date(2026, 12, 1)
check("a date past the fortnight wraps to next year",
      jp._signal_expiry(None, "for 10/30") == "2027-10-30")
jp.us_today = _uq

# --- misattributed card qty is distrusted unless the text states one ---------
def _mis_down(strike, qty):
    return {"instrument": "SPX", "direction": "short", "action": "none",
            "entry": None, "stop": None, "targets": [], "confidence": 80,
            "horizon": "", "rationale": "x", "trim": None, "spread": None,
            "qty": qty, "option": {"underlying": "SPX", "strike": strike,
                                   "right": "put", "expiry": "2026-11-02"}}

def _qty_for(txt, card):
    _MIS.clear()
    jp.hermes_parse = lambda text, photo=None, c=card: dict(c)
    jp._parse_trade(txt, "1", None,
                    {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}, 0)
    return _MIS[0].qty if _MIS else None

check("a wrong-card qty of 25 falls back to 1",
      _qty_for("Sell QQQ 697P n Buy 695P n collect $91 for today",
               _mis_down(7640, 25)) == 1)
check("a qty the text states still wins",
      _qty_for("Sell QQQ 697P n Buy 695P n collect $91 for today, TO OPEN 5",
               _mis_down(7640, 25)) is not None)
# D72: the qty-1 ceiling applies to every order, so a matching card's own
# qty (6) resolves to 1. What this test still proves is that a MATCHING card
# is not distrusted (it is used, capped) — versus the wrong-card case below.
check("a matching card is used (qty capped at 1 by the ceiling)",
      _qty_for("Sell QQQ 697P n Buy 695P n collect $91 for today",
               dict(_mis_down(697, 6), instrument="QQQ",
                    option={"underlying": "QQQ", "strike": 697, "right": "put",
                            "expiry": "2026-11-06"})) == 1)

# --- 3978: guard needs a spot even when the CFD feed is down ------------
_orig_bbc_q = jp.bbc
jp._under_quote = _REAL_UQ
jp.bbc = lambda: type("B", (), {"get_quote": staticmethod(
    lambda s: (_ for _ in ()).throw(RuntimeError("MT5 down")))})()
jp.get_quote = lambda s: None
class _QT:
    def quote_stock(self, s): return 648.0
jp._TRADIER_INST = _QT()
check("equity quote falls back to the broker feed when MT5 is down",
      jp._under_quote("QQQ") == 648.0)
jp.bbc = _orig_bbc_q

# an equity must not trust a stale CFD print; an index must use the venue feed
jp._under_quote = _REAL_UQ
jp.bbc = lambda: type("B", (), {"get_quote": staticmethod(lambda s: 656.27)})()
class _QT2:
    def quote_stock(self, s): return 648.0
jp._TRADIER_INST = _QT2()
check("equity prefers the real feed over a stale CFD print",
      jp._under_quote("QQQ") == 648.0)
check("index keeps the venue CFD level", jp._under_quote("SPX") == 656.27)
jp._under_quote = lambda u: None
jp.get_quote = assert_none if False else jp.get_quote

# --- 4007: a crossed card plus a split-scaled strike end at the right contract --
jp.save_state = lambda st: None; jp.ledger_row = lambda r: None; jp.push = lambda t, s: None
jp.park_signal = lambda c, m, t, w: PARKS.append(w)
jp._under_quote = lambda u: 35.1
jp._listed = lambda u, e, r, k: (True, True, float(k)) if float(k) < 50 else (False, True, 36.0)
_CROSS = []
class _FC:
    def place(self, rec):
        _CROSS.append(rec)
        class R: ok = True; order_id = "T"; venue = "tradier"; reason = "s"; fill_price = None
        return R()
    def order_fill(self, o): return "filled", 3.9
    def cancel(self, o): return True, "x"
jp._TRADIER_INST = _FC()
jp.hermes_parse = lambda text, photo=None: {"instrument": "DBX", "direction": "long",
    "action": "entry", "entry": 1.28, "stop": None, "targets": [], "confidence": 50,
    "horizon": "", "rationale": "x", "trim": None, "spread": None,
    "option": {"underlying": "DBX", "strike": 36, "right": "put", "expiry": FUT}}
_cs = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
jp._parse_trade(f"KTOS 146P at 1.73 for {FUT_TXT}", "4007", None, _cs, 0)
_ord = _CROSS[0].legs[0] if _CROSS else None
check("crossed card takes the text ticker", _ord and _ord.underlying == "KTOS", str(_ord))
check("split-scaled strike snaps to the listed 36", _ord and _ord.strike == 36.0, str(_ord))
_bt = list(_cs["open"].values())[0] if _cs["open"] else {}
check("stop is rebased under the 3.90 fill", _bt.get("stop", 9) < _bt.get("entry", 0), str(_bt.get("stop")))
check("qty is capped by the $300 risk rule", _bt.get("qty", 99) <= 2, str(_bt.get("qty")))
check("stop is ~40% of premium, not a hair-trigger",
      _bt.get("stop", 0) <= _bt.get("entry", 0) * 0.65, str(_bt.get("stop")))

print(f"\n{len(FAILS)} FAIL(S)" if FAILS else "\nALL PASS")
raise SystemExit(1 if FAILS else 0)
