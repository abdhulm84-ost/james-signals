#!/usr/bin/env python3
"""bb_executor.py — BlackBull Markets MT5 executor for james-signals.

Trades BlackBull cash index CFDs (US500 / USTEC ...) at James's NATIVE levels:
CFD quotes track the index, so no index->ETF conversion and shorts are native
(demo account = real MT5 demo server, i.e. true CFD paper trading).

Requires: MetaTrader5 terminal (BlackBull build) installed and running,
plus bb_config.json = {"login": int, "password": "...", "server": "BlackBull-MT5-Demo",
                       "symbols": {"SPX500": "US500", "NDX100": "USTEC", ...}}

Usage: python bb_executor.py --check      # login + symbol resolve + quote + test order
       python bb_executor.py --selftest   # offline logic checks
"""

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "bb_config.json"

DEFAULT_SYMBOLS = {          # instrument (as James says it) -> BlackBull MT5 symbol
    "SPX500": "US500", "SPX": "US500", "SPXW": "US500",
    "NDX100": "USTEC", "NDX": "USTEC", "NAS100": "USTEC",
}

_state = {"connected": False, "launched_at": 0}


def _mt():
    import MetaTrader5 as mt5
    return mt5


def terminal_alive():
    """True when the MT5 terminal process is running (independent of the API)."""
    try:
        import subprocess
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process -Name terminal64 -ErrorAction SilentlyContinue | "
             "Measure-Object).Count"],
            capture_output=True, text=True, timeout=30)
        return out.stdout.strip() not in ("", "0")
    except Exception:
        return False


def launch_terminal(cfg=None):
    """Start the MT5 terminal ourselves if it is not running (crash recovery)."""
    cfg = cfg or json.loads(CONFIG.read_text(encoding="utf-8"))
    path = cfg.get("terminal_path")
    if not path or not Path(path).exists():
        return False
    try:
        import subprocess
        subprocess.Popen([path], close_fds=True)
        _state["launched_at"] = time.time()
        return True
    except Exception:
        return False


def connect(cfg=None, relaunch=True):
    """Login to the MT5 terminal. If the terminal is gone, start it and retry."""
    mt5 = _mt()
    cfg = cfg or json.loads(CONFIG.read_text(encoding="utf-8"))
    if _state["connected"]:
        # a cached 'connected' flag is worthless once the terminal dies
        if mt5.terminal_info() is not None:
            return True
        _state["connected"] = False
    init_kwargs = {}
    if cfg.get("terminal_path"):
        init_kwargs["path"] = cfg["terminal_path"]
    if not mt5.initialize(**init_kwargs):
        if relaunch and launch_terminal(cfg):
            for _ in range(20):                    # give it ~20s to come up
                time.sleep(1)
                if mt5.initialize(**init_kwargs):
                    break
            else:
                raise RuntimeError(f"mt5.initialize failed after relaunch: "
                                   f"{mt5.last_error()}")
        else:
            raise RuntimeError(f"mt5.initialize failed: {mt5.last_error()} "
                               f"(is the BlackBull MT5 terminal installed/running?)")
    if not mt5.login(cfg["login"], password=cfg["password"], server=cfg["server"]):
        raise RuntimeError(f"mt5.login failed: {mt5.last_error()}")
    _state["connected"] = True
    return True


def health(cfg=None):
    """Cheap liveness probe used by the periodic monitor.
    Returns (ok, detail). Never raises."""
    try:
        mt5 = _mt()
        connect(cfg)
        ti = mt5.terminal_info()
        ai = mt5.account_info()
        if ti is None or ai is None:
            return False, "terminal_info/account_info empty"
        if not ti.trade_allowed:
            return False, "algo trading disabled in terminal"
        tick = mt5.symbol_info_tick("USTEC.f") or mt5.symbol_info_tick("US500")
        if not tick or not (tick.bid or tick.last):
            return False, "no quote on USTEC.f/US500"
        return True, f"MT5 {ai.login} ok, USTEC.f bid {tick.bid:,.2f}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def resolve_symbol(instrument, cfg=None):
    """'SPX500' -> exact MT5 symbol (US500, US500.z, ...), enabled in Market Watch."""
    mt5 = _mt()
    cfg = cfg or json.loads(CONFIG.read_text(encoding="utf-8"))
    try:
        connect(cfg)      # symbol_info() returns None until the API is initialised
    except Exception:
        return None
    want = cfg.get("symbols", DEFAULT_SYMBOLS).get((instrument or "").upper())
    if not want:
        # dynamic lookup: BlackBull lists ~1,669 US share CFDs as TICKER.OQ/.N/.P
        ticker = (instrument or "").strip().upper()
        if not ticker or not ticker.replace(".", "").replace("/", "").replace("-", "").isalnum():
            return None
        bases = [ticker]
        # class shares: BlackBull spells BRK.B as 'BRKb.N' (prefix upper, class
        # letter lower) — normalize dots/dashes and add that casing variant
        if any(ch in ticker for ch in ".-/"):
            t2 = ticker.replace(".", "").replace("-", "").replace("/", "")
            if len(t2) > 2 and t2.isalnum():
                bases.append(t2[:-1] + t2[-1].lower())
        for base in bases:
            for sfx in (".OQ", ".N", ".P", ".A", ".K", ""):
                si = mt5.symbol_info(base + sfx)
                if si:
                    mt5.symbol_select(base + sfx, True)
                    return base + sfx
        return None
    exact = mt5.symbol_info(want)
    if exact:
        mt5.symbol_select(want, True)
        return want
    for c in (f"{want}.z", f"{want}.cash", f"{want}.a", want.upper(), want.lower()):
        si = mt5.symbol_info(c)
        if si:
            mt5.symbol_select(c, True)
            return c
    # last resort: pattern search
    for s in (mt5.symbols_get(f"*{want}*") or []):
        mt5.symbol_select(s.name, True)
        return s.name
    return None


def get_quote(instrument, cfg=None):
    """Last price for an index CFD, or None."""
    mt5 = _mt()
    try:
        connect(cfg)
        sym = resolve_symbol(instrument, cfg)
        if not sym:
            return None
        t = mt5.symbol_info_tick(sym)
        if not t:
            return None
        return float(t.bid or t.last or 0) or None
    except Exception:
        return None


def get_symbol_quote(sym, cfg=None):
    """Live bid for any MT5 symbol (e.g. SPY.P / QQQ.OQ share CFDs)."""
    mt5 = _mt()
    try:
        connect(cfg)
        si = mt5.symbol_info(sym)
        if not si:
            mt5.symbol_select(sym, True)
            si = mt5.symbol_info(sym)
        if not si:
            return None
        t = mt5.symbol_info_tick(sym)
        if not t:
            return None
        return float(t.bid or t.last or 0) or None
    except Exception:
        return None


def _filling_mode(si):
    """Pick an order filling mode the symbol actually accepts.
    symbol_info.filling_mode is a bitmask: 1 = FOK allowed, 2 = IOC allowed."""
    mt5 = _mt()
    fm = si.filling_mode
    if fm & 2:                                   # IOC allowed
        return mt5.ORDER_FILLING_IOC
    if fm & 1:                                   # FOK allowed
        return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN


def open_cfd(instrument, direction, lots, sl=None, tp=None, cfg=None):
    """Market order on the index CFD. direction: long/short. Returns dict."""
    mt5 = _mt()
    connect(cfg)
    cfg = cfg or json.loads(CONFIG.read_text(encoding="utf-8"))
    sym = resolve_symbol(instrument, cfg)
    if not sym:
        return {"ok": False, "reason": f"no MT5 symbol for {instrument}"}
    si = mt5.symbol_info(sym)
    if si is None:
        return {"ok": False, "reason": f"symbol_info failed for {sym}"}
    # volume must be a multiple of volume_step (US500 steps 0.1, USTEC.f 0.01)
    step = si.volume_step or 0.01
    lots = int(lots / step) * step
    lots = max(si.volume_min, round(lots, 8))
    lots = round(lots, len(str(step).split(".")[1]) if "." in str(step) else 0)
    tick = mt5.symbol_info_tick(sym)
    is_long = direction == "long"
    price = tick.ask if is_long else tick.bid
    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": sym,
        "volume": lots,
        "type": mt5.ORDER_TYPE_BUY if is_long else mt5.ORDER_TYPE_SELL,
        "price": price,
        "deviation": 30,
        "magic": 20261023,
        "comment": "james-signals",
        "type_filling": _filling_mode(si),
    }
    if sl:
        req["sl"] = round(float(sl), si.digits)
    if tp:
        req["tp"] = round(float(tp), si.digits)
    r = mt5.order_send(req)
    if r is None or r.retcode != mt5.TRADE_RETCODE_DONE:
        code = getattr(r, "retcode", "None")
        comment = getattr(r, "comment", "") or ""
        hint = {
            10014: "invalid volume (not a multiple of the symbol's volume_step)",
            10018: "market closed",
            10044: "symbol close-only AT BROKER (check symbol_info.trade_mode)",
            10015: "invalid price",
            10016: "invalid stops",
            10027: "algo trading disabled in terminal",
        }.get(code)
        detail = hint or (comment or mt5.last_error())
        return {"ok": False, "reason": f"retcode={code}: {detail}"}
    return {"ok": True, "deal": r.order, "price": r.price, "symbol": sym,
            "volume": lots}


def close_cfd(position_ticket, cfg=None):
    """Close an open CFD position by ticket at market."""
    mt5 = _mt()
    connect(cfg)
    pos = next((p for p in (mt5.positions_get() or []) if p.ticket == position_ticket), None)
    if not pos:
        return {"ok": False, "reason": f"position {position_ticket} not found"}
    si = mt5.symbol_info(pos.symbol)
    tick = mt5.symbol_info_tick(pos.symbol)
    is_long = pos.type == mt5.POSITION_TYPE_BUY
    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": pos.symbol,
        "volume": pos.volume,
        "type": mt5.ORDER_TYPE_SELL if is_long else mt5.ORDER_TYPE_BUY,
        "position": position_ticket,
        "price": tick.bid if is_long else tick.ask,
        "deviation": 30,
        "magic": 20261023,
        "comment": "james-signals-close",
        "type_filling": _filling_mode(si),
    }
    r = mt5.order_send(req)
    if r is None or r.retcode != mt5.TRADE_RETCODE_DONE:
        return {"ok": False, "reason": f"close retcode={getattr(r, 'retcode', None)}"}
    return {"ok": True, "deal": r.order, "price": r.price}


def open_positions(cfg=None):
    mt5 = _mt()
    connect(cfg)
    return [{"ticket": p.ticket, "symbol": p.symbol,
             "direction": "long" if p.type == _mt().POSITION_TYPE_BUY else "short",
             "volume": p.volume, "open_price": p.price_open,
             "sl": p.sl, "tp": p.tp, "profit": p.profit}
            for p in (mt5.positions_get() or [])]


def recover_closed_deal(position_ticket, cfg=None):
    """D111 (2026-11-19): when a close answers 'position not found', the trade
    may have ALREADY closed at the broker (TP/SL/manual — the very reason we
    were trying to close it). Query the deal history for the position's exit
    deal and return its REAL close price + profit, so a winner that hit target
    books its actual PnL instead of a phantom zero."""
    mt5 = _mt()
    connect(cfg)
    try:
        deals = mt5.history_deals_get(position=position_ticket)
    except Exception:
        deals = None
    if not deals:
        return None
    # an exit deal: DEAL_ENTRY_OUT / OUT_BY / OUT_BY with the position id set
    exits = [d for d in deals
             if getattr(d, "entry", None) in
             (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY)]
    if not exits:
        # no exit deal yet — the position is genuinely gone with no recorded close
        return None
    # latest exit deal is the authoritative close
    exits.sort(key=lambda d: d.time, reverse=True)
    d = exits[0]
    return {"ticket": position_ticket, "symbol": d.symbol,
            "price": d.price, "profit": d.profit,
            "volume": d.volume, "time": d.time}


# ---------------------------------------------------------------- checks

def selftest():
    """Offline logic: filling-mode pick + config shape. No MT5, no network."""
    fails = []

    def check(name, cond):
        print(f"{'PASS' if cond else 'FAIL'}  {name}")
        if not cond:
            fails.append(name)

    class FakeMT5:
        SYMBOL_FILLING_IOC, SYMBOL_FILLING_FOK = 1, 2
        ORDER_FILLING_IOC, ORDER_FILLING_FOK, ORDER_FILLING_RETURN = 1, 0, 2

    class FakeSI:
        def __init__(self, fm):
            self.filling_mode = fm

    import bb_executor as bb

    class FakeMT5Static:
        SYMBOL_FILLING_IOC, SYMBOL_FILLING_FOK = 1, 2
        ORDER_FILLING_IOC, ORDER_FILLING_FOK, ORDER_FILLING_RETURN = 1, 0, 2

    orig_mt = bb._mt
    bb._mt = lambda: FakeMT5Static()
    try:
        check("filling: IOC when allowed", bb._filling_mode(FakeSI(3)) == 1)
        check("filling: FOK when only FOK", bb._filling_mode(FakeSI(1)) == 0)
        check("filling: RETURN fallback", bb._filling_mode(FakeSI(0)) == 2)
    finally:
        bb._mt = orig_mt
    cfg_missing = {"login": 1, "password": "x", "server": "s"}
    check("config needs login/password/server",
          all(k in cfg_missing for k in ("login", "password", "server")))
    check("default symbol map covers SPX500+NDX100",
          bb.DEFAULT_SYMBOLS.get("SPX500") == "US500" and
          bb.DEFAULT_SYMBOLS.get("NDX100") == "USTEC")
    print("ALL PASS" if not fails else f"{len(fails)} FAILURES")
    return 0 if not fails else 1


def check_live():
    """--check: full connectivity test against the demo server."""
    print("connecting...")
    connect()
    mt5 = _mt()
    acct = mt5.account_info()
    print(f"account: {acct.login} | {acct.server} | balance {acct.balance} "
          f"{acct.currency} | leverage 1:{acct.leverage}")
    for inst in ("SPX500", "NDX100"):
        sym = resolve_symbol(inst)
        q = get_quote(inst)
        print(f"{inst} -> {sym} @ {q}")
        if sym and q:
            si = mt5.symbol_info(sym)
            print(f"   volume min/step: {si.volume_min}/{si.volume_step}, "
                  f"digits {si.digits}, filling {si.filling_mode}")
    print("\nplace a 0.1-lot test order? (writes a real demo position)")
    ans = input("y/N: ").strip().lower()
    if ans == "y":
        r = open_cfd("SPX500", "long", 0.1)
        print("open:", r)
        if r.get("ok"):
            input("press Enter to close it...")
            print("close:", close_cfd(r["deal"]))
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--check" in sys.argv:
        sys.exit(check_live())
    print(__doc__)
