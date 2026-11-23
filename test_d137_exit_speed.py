"""test_d137_exit_speed.py — D137/D138/D139 guards (2026-11-21).

D137: every Tradier close carries a LIMIT (credit spread = min(width, 1.30x
      mark); single = 1.30x/0.70x band; no mark -> D135 width / market), and
      manage_once re-prices a resting unfilled close against fresh marks
      (max 2), then escapes to market — exits wait a cycle, never strand.
D138: deterministic exits skip hermes/vision entirely (measured 167-324s
      James's exit words -> our close order, most of it the parse). Gate:
      classify=exit/trim AND text-scoped scope, or exactly one open
      instrument; multi-instrument bare text keeps the brain.
D139: single-option entries go out as LIMIT (signal +/-15% — singles were the
      last market entries), and any fill >25% from the signal price pushes a
      divergence alert (MRNA +29.3% and WMT -48% both booked silently).

Run: python test_d137_exit_speed.py   (exit 0 = green)
"""
import inspect
import pathlib
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import james_pipe as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


_src = pathlib.Path(jp.__file__).read_text(encoding="utf-8", errors="replace")

# ---- D137: close carries the right limit ----------------------------------
_CLOSES = []
_PUSH, _LED, _LOG, _SAVE = jp.push, jp.ledger_row, jp.log, jp.save_state
_jp_tr, _jp_sm, _jp_om = jp._TRADIER, jp.spread_mark, jp.opt_mark
# D140: close_trade now asks for a real-spot anchor — stub it OFF for the
# D137 cases (existing limit math) and flip _ANCHOR["v"] in the D140 cases.
# Keeps the whole suite offline: no quote feeds, no network. The REAL
# _anchor_spot is kept for the band checks below (the stub would make them
# pass vacuously).
_ANCHOR = {"v": None}
_jp_as_real = jp._anchor_spot
jp._anchor_spot = lambda under, legs: _ANCHOR["v"]


class _CapClose:
    def __init__(self, ok=True):
        self.ok = ok

    def close(self, t, limit_price=None, qty=None):
        _CLOSES.append((limit_price, qty))
        return SimpleNamespace(ok=self.ok, order_id=99001 if self.ok else None,
                               reason="stub")

    def cancel(self, oid):
        return True, "stub cancelled"


def _run_close(t, mark):
    jp.push = lambda *a, **k: None
    jp.ledger_row = lambda *a, **k: None
    jp.log = lambda *a, **k: None
    jp.save_state = lambda *a, **k: None
    jp.spread_mark = lambda x: mark
    jp.opt_mark = lambda x: mark
    jp._TRADIER = lambda: _CapClose(ok=True)
    try:
        jp.close_trade(dict(t), 0.0, "guard", {})
    finally:
        jp.push, jp.ledger_row, jp.log, jp.save_state = _PUSH, _LED, _LOG, _SAVE
        jp._TRADIER, jp.spread_mark, jp.opt_mark = _jp_tr, _jp_sm, _jp_om


_base = {"id": "TX", "executor": "tradier", "status": "open_tradier",
         "qty": 1, "instrument": "SPXW", "msg_id": "9500"}

_run_close({**_base, "direction": "short", "legs": [[7355.0, "put", "sell"],
                                                    [7335.0, "put", "buy"]]}, 15.0)
check("D137: credit spread close = min(width 20, 1.30x mark 15 -> 19.5)",
      _CLOSES and _CLOSES[-1][0] == 19.5, str(_CLOSES[-1] if _CLOSES else None))

_run_close({**_base, "direction": "short", "legs": [[7355.0, "put", "sell"],
                                                    [7335.0, "put", "buy"]]}, 30.0)
check("D137: width ceiling wins when mark exceeds it (30 -> 20)",
      _CLOSES and _CLOSES[-1][0] == 20.0, str(_CLOSES[-1]))

_run_close({**_base, "direction": "short", "legs": [[7355.0, "put", "sell"],
                                                    [7335.0, "put", "buy"]]}, None)
check("D137: no mark -> width cap still applies (D135 fallback)",
      _CLOSES and _CLOSES[-1][0] == 20.0, str(_CLOSES[-1]))

_run_close({**_base, "direction": "short", "opt_id": "WMT 261121C00128000",
            "legs": [[128.0, "call", "sell"]]}, 1.00)
check("D137: short single close = 1.30x mark cap (buy-back band)",
      _CLOSES and _CLOSES[-1][0] == 1.3, str(_CLOSES[-1]))

_run_close({**_base, "direction": "long", "opt_id": "WMT 261121C00128000",
            "legs": [[128.0, "call", "buy"]]}, 1.00)
check("D137: long single close = 0.70x mark floor (sell band)",
      _CLOSES and _CLOSES[-1][0] == 0.7, str(_CLOSES[-1]))

_run_close({**_base, "direction": "long", "opt_id": "WMT 261121C00128000",
            "legs": [[128.0, "call", "buy"]]}, None)
check("D137: no mark -> single stays market (never block a dead feed)",
      _CLOSES and _CLOSES[-1][0] is None, str(_CLOSES[-1]))

_run_close({**_base, "direction": "long", "legs": [[7200.0, "call", "buy"],
                                                   [7220.0, "call", "sell"]]}, 5.0)
check("D137: long/debit vertical stays market (close is a credit order)",
      _CLOSES and _CLOSES[-1][0] is None, str(_CLOSES[-1]))

# lim bookkeeping after successful placement
_run_close({**_base, "direction": "short", "legs": [[7355.0, "put", "sell"],
                                                    [7335.0, "put", "buy"]]}, 15.0)
_t_after = {**_base, "direction": "short", "legs": [[7355.0, "put", "sell"],
                                                   [7335.0, "put", "buy"]]}
jp.push = lambda *a, **k: None
jp.ledger_row = lambda *a, **k: None
jp.log = lambda *a, **k: None
jp.save_state = lambda *a, **k: None
jp.spread_mark = lambda x: 15.0
jp._TRADIER = lambda: _CapClose(ok=True)
try:
    jp.close_trade(_t_after, 0.0, "guard", {})
    check("D137: placement records lim_at/lim_qty for the refresh",
          _t_after.get("lim_at") and _t_after.get("lim_qty") == 1
          and _t_after.get("lim_refresh") == 0,
          str({k: _t_after.get(k) for k in ("lim_at", "lim_qty", "lim_refresh")}))
finally:
    jp.push, jp.ledger_row, jp.log, jp.save_state = _PUSH, _LED, _LOG, _SAVE
    jp._TRADIER, jp.spread_mark, jp.opt_mark = _jp_tr, _jp_sm, _jp_om

# ---- D140: real-spot anchor caps the close below the band -----------------
check("D140: put OTM -> sold-leg intrinsic + tv allowance (15-wide -> 3.75)",
      jp.anchor_close_cap([(7170.0, "put", "sell"), (7155.0, "put", "buy")],
                          7190.0) == 3.75)
check("D140: deep-ITM floors at the width (structural max of the package)",
      jp.anchor_close_cap([(7170.0, "put", "sell"), (7155.0, "put", "buy")],
                          7100.0) == 15.0
      and jp.anchor_close_cap([(663.0, "put", "sell"), (661.0, "put", "buy")],
                              645.0) == 2.0)
check("D140: call OTM -> intrinsic 0 + allowance",
      jp.anchor_close_cap([(7365.0, "call", "sell"), (7385.0, "call", "buy")],
                          7300.0) == 5.0)
check("D140: narrow-spread allowance floors at 1.0",
      jp.anchor_close_cap([(663.0, "put", "sell"), (661.0, "put", "buy")],
                          670.0) == 1.0)
check("D140: unprovable shapes -> None (mark band stands, never blocks)",
      jp.anchor_close_cap([(7200.0, "call", "buy"), (7220.0, "call", "buy")],
                          7300.0) is None
      and jp.anchor_close_cap([(7200.0, "call", "buy"), (7155.0, "put", "buy")],
                              7300.0) is None
      and jp.anchor_close_cap([(7170.0, "put", "sell"), (7155.0, "put", "buy")],
                              None) is None)

# the band itself (REAL _anchor_spot): the D133 RUT garbage print must never
# anchor — the stub above would make these pass vacuously.
jp._anchor_spot = _jp_as_real
_jp_uq = jp._under_quote
jp._under_quote = lambda u: 7151.30       # SPX-level number on a RUT query
check("D140: band rejects the D133 garbage print (RUT vs SPX-level spot)",
      jp._anchor_spot("RUTW", [(2665.0, "put", "sell"),
                               (2655.0, "put", "buy")]) is None)
jp._under_quote = lambda u: 2660.0
check("D140: plausible spot passes through to the anchor",
      jp._anchor_spot("RUTW", [(2665.0, "put", "sell"),
                               (2655.0, "put", "buy")]) == 2660.0)
jp._under_quote = _jp_uq
jp._anchor_spot = lambda under, legs: _ANCHOR["v"]   # back to the stub

_ANCHOR["v"] = 7360.0      # sold 7355P OTM -> intrinsic 0 -> bound = 5.0
_run_close({**_base, "direction": "short", "legs": [[7355.0, "put", "sell"],
                                                    [7335.0, "put", "buy"]]}, 15.0)
check("D140: close limit = min(mark band 19.5, real-spot bound 5.0) -> 5.0",
      _CLOSES and _CLOSES[-1][0] == 5.0, str(_CLOSES[-1]))
_ANCHOR["v"] = None        # anchor off for everything below

# ---- D137 manage-side refresh (bounded reprice -> market escape) ----------
_RECLOSES = []


class _Cap2:
    def close(self, t, limit_price=None, qty=None):
        _RECLOSES.append(limit_price)
        return SimpleNamespace(ok=True, order_id=99002, reason="stub")

    def cancel(self, oid):
        return True, "cancelled"


_jp_tr2, jp2_push, jp2_log, jp2_save, jp2_led = jp._TRADIER, jp.push, jp.log, jp.save_state, jp.ledger_row
jp.push = lambda *a, **k: None
jp.log = lambda *a, **k: None
jp.save_state = lambda *a, **k: None
jp.ledger_row = lambda *a, **k: None
jp._TRADIER = lambda: _Cap2()
_st_ct = jp._tradier_close_status
jp._tradier_close_status = lambda oid: ("Working", None)
jp.spread_mark = lambda x: 15.0
try:
    _t_r = {**_base, "direction": "short", "legs": [[7355.0, "put", "sell"],
                                                    [7335.0, "put", "buy"]],
            "expiry": "2027-01-15", "status": "close_pending",
            "close_order_id": "777", "close_reason": "stop hit",
            "lim_at": time.time() - 61, "lim_refresh": 0, "lim_qty": 1}
    _st = {"open": {"TX": _t_r}, "closed": {}}
    jp.manage_once(_st)
    check("D137 refresh: unfilled resting close is cancelled and re-priced",
          _t_r.get("lim_refresh") == 1 and _t_r.get("close_order_id") == 99002
          and _RECLOSES and _RECLOSES[-1] == 19.5,
          str({"r": _t_r.get("lim_refresh"), "oid": _t_r.get("close_order_id"),
               "limits": _RECLOSES}))
    check("D137 refresh: fresh lim_at set, status still close_pending",
          time.time() - float(_t_r.get("lim_at") or 0) < 5
          and _t_r.get("status") == "close_pending"
          and not _t_r.get("_reprice"),
          str(_t_r.get("status")))
    # escape leg: 2 prior re-prices + aged -> market
    _t_r["lim_refresh"] = 2
    _t_r["lim_at"] = time.time() - 61
    jp.manage_once(_st)
    check("D137 escape: 3rd placement is MARKET (limit None) with flag",
          _t_r.get("lim_refresh") == 3 and _t_r.get("_lim_escape")
          and _RECLOSES[-1] is None,
          str({"r": _t_r.get("lim_refresh"), "last": _RECLOSES[-1]}))
    # backoff: fresh lim_at -> no churn
    _n = len(_RECLOSES)
    _t_r["lim_at"] = time.time()          # just placed — inside the 60s window
    jp.manage_once(_st)
    check("D137 refresh: inside 60s window no churn",
          len(_RECLOSES) == _n, str(len(_RECLOSES) - _n))
finally:
    jp._TRADIER, jp.push, jp.log, jp.save_state, jp.ledger_row = _jp_tr2, jp2_push, jp2_log, jp2_save, jp2_led
    jp._tradier_close_status = _st_ct
    jp.spread_mark = _jp_sm

# ---- D138: exit fast-path gate + no-brain dispatch ------------------------
check("D138: text-scoped exit skips the brain",
      jp._exit_fast("take gains on mrna puts", {"open": {}}) == "")
check("D138: trim command fast",
      jp._exit_fast("trim 80% on FISV", {"open": {}}) == "")
check("D138: bare exit + ONE open instrument injects it",
      jp._exit_fast("take gains", {"open": {"A": {"instrument": "INTU"},
                                            "B": {"instrument": "QQQ"}}}) is None
      and jp._exit_fast("take gains", {"open": {"A": {"instrument": "INTU"}}}) == "INTU")
check("D138: entries never fast-path",
      jp._exit_fast("buy WMT 128C at 1.7", {"open": {}}) is None)
check("D138: %-status never fast-path (D134 interplay)",
      jp._exit_fast("10% up", {"open": {"A": {"instrument": "INTU"}}}) is None)
check("D138: image-only / empty text never fast-path",
      jp._exit_fast("", {"open": {}}) is None)

# end-to-end: _parse_trade routes a scoped exit WITHOUT hermes
_HERMES_CALLED = []
_EXIT_CALL = []
_jp_hermes, jp2_map = jp.hermes_parse, jp.is_map_message
_jp_hu, jp2_book = jp.handle_exit_update, jp.book_apply
_jp_arm = jp.arm_exit_targets
_jp_sv, _jp_pu, _jp_ld = jp.save_state, jp.push, jp.ledger_row
jp.hermes_parse = lambda *a, **k: (_HERMES_CALLED.append(1), None)[1]
jp.handle_exit_update = lambda act, norm, msg_id, st, text="": _EXIT_CALL.append(
    (act, (norm or {}).get("instrument"), text))
jp.book_apply = lambda *a, **k: None
jp.save_state = lambda *a, **k: None          # D115: never write state.json from a test
jp.push = lambda *a, **k: None
jp.ledger_row = lambda *a, **k: None
try:
    jp._parse_trade("take gains on mrna puts", "9600", None, {}, 0)
    check("D138 e2e: hermes never called for a scoped exit",
          not _HERMES_CALLED, str(_HERMES_CALLED))
    check("D138 e2e: exit routed to handle_exit_update (text scope)",
          _EXIT_CALL and _EXIT_CALL[0][0] == "exit"
          and _EXIT_CALL[0][2] == "take gains on mrna puts",
          str(_EXIT_CALL))
finally:
    jp.hermes_parse, jp.handle_exit_update, jp.book_apply = _jp_hermes, _jp_hu, jp2_book
    jp.is_map_message, jp.arm_exit_targets = jp2_map, _jp_arm
    jp.save_state, jp.push, jp.ledger_row = _jp_sv, _jp_pu, _jp_ld

# ---- D139: entry divergence warn + source guards ---------------------------
_PUSH2, _LED2 = jp.push, jp.ledger_row
_AL, _PL = [], []
jp.ledger_row = lambda r: _AL.append(r)
jp.push = lambda *a, **k: _PL.append(a[0] if a else "")
jp.log = lambda *a, **k: None
try:
    _tw = {"id": "TW", "msg_id": "9601", "instrument": "MRNA", "entry": 3.48}
    jp._warn_entry_fill(_tw, 4.5, 3.48, {})
    check("D139: MRNA-class fill (+29.3% over signal) pushes",
          _PL and "D139" in _PL[-1] and "MRNA" in _PL[-1], str(_PL))
    check("D139: warn is recorded as an ALERT row",
          _AL and _AL[-1].get("event") == "ALERT" and "D139" in (_AL[-1].get("reason") or ""),
          str(_AL[-1] if _AL else None))
    jp._warn_entry_fill(_tw, 5.0, 3.48, {})
    check("D139: once per trade (second fill silent)",
          len([p for p in _PL if "D139" in p]) == 1, str(_PL))
    _PL.clear()
    jp._warn_entry_fill({"id": "TW2"}, 1.7, 1.8, {})
    check("D139: inside-band drift stays quiet",
          not any("D139" in p for p in _PL), str(_PL))
    jp._warn_entry_fill({"id": "TW3"}, 0.88, 1.7, {})
    check("D139: WMT-class fill (-48% under signal) pushes",
          any("D139" in p and "-48%" in p for p in _PL), str(_PL))
finally:
    jp.push, jp.ledger_row = _PUSH2, _LED2

check("source: singles carry the buffered entry limit (D139)",
      "0.85 if _short else 1.15" in _src)
check("source: manage_once holds the D137 reprice+escape",
      "D137 reprice" in _src and "_lim_escape" in _src)
check("source: three recheck fills warn on divergence",
      _src.count("_warn_entry_fill(t, fill, _sig_e, st)") == 3)
import james_exec as je
_src_e = pathlib.Path(je.__file__).read_text(encoding="utf-8", errors="replace")
check("source: executor single-option entry honours the limit (D139)",
      '"limit" if _lmt else "market"' in _src_e)

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
