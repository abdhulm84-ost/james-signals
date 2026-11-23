"""test_d134_status.py — D133/D134/D135/D136 guards (2026-11-21).

D134: a bare '<n>% up' mark is a STATUS, never an entry — msg 4535 ('10% up')
      and 4522 ('30% up on bull puts') mirrored JAMES's position screenshots
      and entered after the move (TL4535 -100, TL4522 -50 on TL4520's own
      7315/7300 legs). The text-first guard now kills the card AND the entry
      gate refuses it even if a text structure would satisfy the gate; only a
      real order ticket ('TO OPEN') may override.
D135: spread closes went out MARKET and legged in Friday's crash open — the
      QQQ pair filled 9.31 NET on a 2-wide spread, booking -32,072 against a
      -7,941 defined-risk bound. Credit-spread closes (full and partial) now
      carry a debit limit AT the width: impossible packages blocked, every
      legitimate fill still crosses.
D136: manage_shadow passed a DEBIT's cost as a positive 'credit' — SIM4527
      (paid 13.5, expired worthless at SPX 7,175.89) read +1,350 'MISSED
      TRADE' when the truth is -1,350 (ack was right). Shadows now sign from
      leg geometry, like settle_parked's is_debit rule.
D133: BlackBull's RUT print returned 7,151.30 (SPX's own level) and the far-
      strike guard called 4540's 2665P '62.7% from spot'. Index roots now
      refuse a spot so far from every strike (bad print, not far strike).

Run: python test_d134_status.py   (exit 0 = green)
"""
import inspect
import pathlib
import sys
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import james_pipe as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


# ---- D134: %-status text never carries entry intent ------------------------
def _spread_card():
    return {"direction": "short", "action": "entry", "instrument": "SPXW",
            "spread": {"type": "vertical", "legs": [
                {"strike": 7170.0, "right": "put", "action": "sell"},
                {"strike": 7155.0, "right": "put", "action": "buy"}]}}


ok, reason, norm = jp.validate_card(_spread_card(), "10% up")
check("validate_card accepts the card shape", ok and norm is not None, reason)
check("D134: '10% up' kills the spread card",
      ok and isinstance(norm, dict) and norm.get("spread") is None,
      str(norm and norm.get("spread")))
check("D134: pct status flagged on norm",
      ok and isinstance(norm, dict) and norm.get("_pct_status") is True)

ok2, _, norm2 = jp.validate_card(_spread_card(), "30% up on bull puts")
check("D134: family word does not resurrect a %-status card",
      ok2 and isinstance(norm2, dict) and norm2.get("spread") is None,
      str(norm2 and norm2.get("spread")))

ok3, _, norm3 = jp.validate_card(_spread_card(), "25% up")
check("D134: '25% up' (the 4527 caption) kills the card too",
      ok3 and isinstance(norm3, dict) and norm3.get("spread") is None)

ok4, _, norm4 = jp.validate_card(_spread_card(),
                                  "buy WMT 128C at 1.7 targets 2.1/2.7/3.5 SL 0.85")
check("control: entry verb keeps the card (unverified survives)",
      ok4 and isinstance(norm4, dict)
      and norm4.get("spread") is not None
      and norm4["spread"].get("_unverified") is True,
      str(norm4 and norm4.get("spread")))

ok5, _, norm5 = jp.validate_card(_spread_card(), "30% up, pl book gains")
check("control: % + ACTION_VERB is an exit path, not pct-status (D121 intact)",
      ok5 and isinstance(norm5, dict) and not norm5.get("_pct_status"),
      str(norm5 and norm5.get("_pct_status")))

check("pct-positive on the real texts only",
      jp.entry_language("10% up") is False)
_src_module = pathlib.Path(jp.__file__).read_text(encoding="utf-8", errors="replace")
check("source: validate_card computes _pct_status (D134)",
      "_pct_status" in jp.validate_card.__doc__ or "_pct_status" in
      inspect.getsource(jp.validate_card))
check("source: entry gate carries the D134 belt",
      "_pct_status" in jp._parse_trade.__doc__ or "_pct_status" in
      inspect.getsource(jp._parse_trade))

# ---- D135: credit-spread closes carry a debit limit at width ---------------
_CLOSES = []


class _CapClose:
    def close(self, t, limit_price=None, qty=None):
        _CLOSES.append((limit_price, qty, len(t.get("legs") or [])))
        return SimpleNamespace(ok=False, order_id=None, reason="stub refused")


_jp_push, _jp_log, _jp_save = jp.push, jp.log, jp.save_state
_jp_led, _jp_tr = jp.ledger_row, jp._TRADIER
jp.push = lambda *a, **k: None
jp.log = lambda *a, **k: None
jp.save_state = lambda *a, **k: None
jp.ledger_row = lambda *a, **k: None
jp._TRADIER = lambda: _CapClose()
try:
    t_spread = {"id": "TW1", "executor": "tradier", "status": "open_tradier",
                "direction": "short", "qty": 9, "instrument": "SPXW",
                "legs": [[7355.0, "put", "sell"], [7335.0, "put", "buy"]]}
    jp.close_trade(dict(t_spread), 0, "stop test", {})
    check("D135: credit-spread close is a debit limit AT width (20)",
          _CLOSES and _CLOSES[-1][0] == 20.0 and _CLOSES[-1][1] == 9,
          str(_CLOSES[-1] if _CLOSES else None))

    t_single = {"id": "TW2", "executor": "tradier", "status": "open_tradier",
                "direction": "short", "qty": 1, "instrument": "WMT",
                "legs": [[128.0, "call", "sell"]]}
    jp.close_trade(dict(t_single), 0, "stop test", {})
    check("control: single-leg close stays market (limit None)",
          _CLOSES and _CLOSES[-1][0] is None, str(_CLOSES[-1]))

    t_long = {"id": "TW3", "executor": "tradier", "status": "open_tradier",
              "direction": "long", "qty": 1, "instrument": "SPXW",
              "legs": [[7200.0, "call", "buy"], [7220.0, "call", "sell"]]}
    jp.close_trade(dict(t_long), 0, "stop test", {})
    check("control: long/debit vertical close stays market (credit order, no width cap)",
          _CLOSES and _CLOSES[-1][0] is None, str(_CLOSES[-1]))
finally:
    jp.push, jp.log, jp.save_state = _jp_push, _jp_log, _jp_save
    jp.ledger_row, jp._TRADIER = _jp_led, _jp_tr

check("source: close_trade holds the D135 width cap",
      "D135" in inspect.getsource(jp.close_trade))

# ---- D136: debit shadows sign correctly -----------------------------------
check("D136: SIM4527 legs classified debit",
      jp._legs_are_debit([(7200.0, "call", "buy"), (7220.0, "call", "sell")]) is True)
check("D136: QQQ credit pair stays credit",
      jp._legs_are_debit([(663.0, "put", "sell"), (661.0, "put", "buy")]) is False)
check("D136: put credit pair (sell high/buy low) stays credit",
      jp._legs_are_debit([(7315.0, "put", "sell"), (7300.0, "put", "buy")]) is False)
check("D136: call credit pair (sell low/buy high) stays credit",
      jp._legs_are_debit([(7330.0, "call", "sell"), (7340.0, "call", "buy")]) is False)
check("D136: single leg / mixed actions fall back to credit",
      jp._legs_are_debit([(128.0, "call", "buy")]) is False
      and jp._legs_are_debit([(7200.0, "call", "buy"), (7220.0, "call", "buy")]) is False)
check("D136: debit shadow at SPX 7175.89 books -1350 (was +1350)",
      jp._spread_expiry_pnl([(7200.0, "call", "buy"), (7220.0, "call", "sell")],
                            -13.5, 1, 7175.89) == -1350.0,
      str(jp._spread_expiry_pnl([(7200.0, "call", "buy"), (7220.0, "call", "sell")],
                                -13.5, 1, 7175.89)))
check("D136: same debit ITM is a real gain (sign both ways)",
      jp._spread_expiry_pnl([(7200.0, "call", "buy"), (7220.0, "call", "sell")],
                            -13.5, 1, 7300.0) == 650.0)
check("control: credit settle unchanged (4521 park math, -880)",
      jp._spread_expiry_pnl([(7315.0, "put", "sell"), (7300.0, "put", "buy")],
                            6.2, 1, 7175.83) == -880.0)
check("source: manage_shadow signs debits (D136)",
      "_legs_are_debit" in inspect.getsource(jp.manage_shadow))

# ---- D133: index spot sanity band ------------------------------------------
check("source: far-strike guard refuses implausible index prints (D133)",
      "bad print, D133" in _src_module and "0.4 * min" in _src_module)

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
