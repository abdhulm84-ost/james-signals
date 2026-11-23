"""
james_exec.py — execution interfaces for the v2 core.

Pure logic lives in james_core.py. This module defines the CONTRACT the venues
implement. Only the BlackBull adapter is live (we have the account); the Tradier
adapter is the multi-leg home and needs a Tradier account + API key to activate.

VENUE MAP (from the review):
  BlackBull  : index/stock CFDs, real-time, native long+short. LIVE.
  Tradier    : multi-leg options (2-4 legs native), real-time data, free API,
               NZ permitted, free sandbox. NEEDS ACCOUNT + KEY.
  Tiger      : legacy single-leg options + paper. DORMANT.

Tradier multi-leg order shape (docs.tradier.com, verified in research):
  POST /v1/accounts/{acct}/orders
    class=multileg   symbol=<underlying>   type=credit|debit|market
    quantity=1   duration=day   price=<net credit>
    option_symbol[0]=<OCC>  side[0]=buy_to_open  quantity[0]=1
    option_symbol[1]=<OCC>  side[1]=sell_to_open  quantity[1]=1
    ... up to 4 legs
  -> places a VERTICAL / FLY / CONDOR as ONE atomic order (no legging-in risk).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from james_core import Record, Leg


@dataclass
class ExecResult:
    ok: bool
    order_id: Optional[str] = None
    fill_price: Optional[float] = None
    venue: str = ""
    reason: str = ""


class Executor(Protocol):
    venue: str

    def place(self, rec: Record) -> ExecResult: ...
    def close(self, position_id: str, fraction: float, limit_price: Optional[float]) -> ExecResult: ...
    def quote(self, underlying: str) -> Optional[float]: ...


# ---------------------------------------------------------------- BlackBull (LIVE)

class BlackBullExecutor:
    """Index/stock CFDs via the MT5 bridge (bb_executor.py). Real-time."""
    venue = "blackbull"

    def place(self, rec: Record) -> ExecResult:
        # wire to bb_executor.open_cfd — already live in the running pipe
        return ExecResult(ok=False, venue=self.venue,
                          reason="wire to bb_executor.open_cfd (live in james_pipe)")

    def close(self, position_id, fraction, limit_price=None):
        return ExecResult(ok=False, venue=self.venue, reason="wire to bb_executor.close_cfd")

    def quote(self, underlying):
        return None  # wire to bb_executor.get_quote


# ---------------------------------------------------------------- Tradier (NEEDS KEY)

@dataclass
class TradierConfig:
    account_id: str
    access_token: str
    sandbox: bool = True          # sandbox = 15-min delayed data; prod = real-time
    base_url: str = "https://sandbox.tradier.com/v1"

    @classmethod
    def load(cls, env="sandbox", path="tradier_config.json"):
        """Load creds from tradier_config.json {prod_account, prod_token,
        sandbox_account, sandbox_token}. env = 'prod' | 'sandbox'."""
        import json
        from pathlib import Path as _P
        d = json.loads(_P(path).read_text(encoding="utf-8"))
        if env == "prod":
            return cls(account_id=d["prod_account"], access_token=d["prod_token"],
                       sandbox=False, base_url="https://api.tradier.com/v1")
        return cls(account_id=d["sandbox_account"], access_token=d["sandbox_token"],
                   sandbox=True, base_url="https://sandbox.tradier.com/v1")



def close_root(trade):
    """Tradier root symbol for a close. Singles store a DISPLAY label in
    'instrument' ('CPNG 15C 11-06') — sending that as the symbol caused
    HTTP 400 'Invalid parameter, symbol' on every single-option close."""
    root = (trade.get("opt_underlying") or trade.get("symbol")
            or trade.get("instrument") or "").split()[0]
    return {"SPX": "SPXW", "NDX": "NDX"}.get(root, root)

class TradierExecutor:
    """Multi-leg options. Native 2-4 leg orders — the fix for B1 (verticals+flies).

    REQUIRES a Tradier account + API key (TradierConfig). Until supplied, place()
    returns a clear not-configured result rather than guessing.
    """
    venue = "tradier"

    def __init__(self, cfg: Optional[TradierConfig] = None):
        self.cfg = cfg

    def place(self, rec: Record) -> ExecResult:
        """Place a real Tradier order: single-leg (1 option) or multileg (2-4).
        Verified live: a 4-leg iron fly places atomically (Tradier calls it a
        condor). Legs are 0-indexed and expiry must be in the future."""
        if not self.cfg:
            return ExecResult(ok=False, venue=self.venue,
                              reason="Tradier not configured — needs account_id + access_token")
        if not self.cfg.sandbox:
            # REAL FUNDS: promotion-ladder gate — fail closed on ANY problem
            # (missing verdict, HOLD milestone, stale file, non-bankable class).
            try:
                from james_pipe import tradier_prod_gate
                _ok, _why = tradier_prod_gate(rec)
            except Exception as _e:
                _ok, _why = False, f"gate error: {type(_e).__name__}: {_e}"
            if not _ok:
                return ExecResult(ok=False, venue=self.venue,
                                  reason=f"PROD GATE: {_why}")
        import requests
        # legs are Leg objects (strike/right/action/qty/underlying/expiry)
        legs = [l for l in (rec.legs or []) if getattr(l, "strike", None)]
        H = {"Authorization": f"Bearer {self.cfg.access_token}",
             "Accept": "application/json"}
        url = f"{self.cfg.base_url}/accounts/{self.cfg.account_id}/orders"
        # expiry must be future or Tradier rejects: "Expiration date must be
        # greater than the current date"
        exp = (rec.expiry or "").replace("-", "")
        if not exp:
            return ExecResult(ok=False, venue=self.venue, reason="no expiry on record")
        try:
            if len(legs) <= 1:
                # single option: one leg, buy_to_open (long) / sell_to_open (short)
                l = legs[0] if legs else None
                if not l:
                    return ExecResult(ok=False, venue=self.venue, reason="no leg")
                side = "buy_to_open" if rec.direction == "long" else "sell_to_open"
                # D139 (2026-11-21): the pipe passes the buffered entry limit
                # (signal +/-15%) in credit — singles were the only entries
                # still going out MARKET (MRNA paid +29% over signal, WMT
                # filled -48% under it, neither ever said out loud). credit is
                # None on callers that want a market order (unchanged).
                _lmt = abs(rec.credit) if rec.credit else None
                data = {"class": "option", "symbol": rec.underlying,
                        "side": side, "quantity": rec.qty or 1,
                        "type": "limit" if _lmt else "market", "duration": "day",
                        "option_symbol": self._occ(l)}
                if _lmt:
                    data["price"] = round(_lmt, 2)
            else:
                # multileg: 0-indexed leg fields
                net = rec.credit or 0.0
                data = {"class": "multileg", "symbol": rec.underlying,
                        "type": "credit" if net > 0 else ("debit" if net < 0 else "market"),
                        "price": abs(net), "duration": getattr(rec, "duration", "day"),
            "quantity": 1}
                for i, l in enumerate(legs[:4]):
                    data[f"option_symbol[{i}]"] = self._occ(l)
                    data[f"side[{i}]"] = self._side(l)
                    data[f"quantity[{i}]"] = l.qty or 1
            if data.get("duration") == "gtc":
                data.pop("close_date", None)      # GTC ignores close_date (gtd is rejected)
            r = requests.post(url, headers=H, data=data, timeout=40)
            try:
                j = r.json()
            except Exception:
                return ExecResult(ok=False, venue=self.venue,
                                  reason=f"Tradier HTTP {r.status_code}: {str(r.text)[:100]}")
            oid = (j.get("order") or {}).get("id")
            if oid:
                return ExecResult(ok=True, order_id=str(oid), venue=self.venue,
                                  reason=f"Tradier {data['class']} order {oid} placed")
            return ExecResult(ok=False, venue=self.venue,
                              reason=f"Tradier rejected: {str(j)[:120]}")
        except Exception as e:
            return ExecResult(ok=False, venue=self.venue,
                              reason=f"Tradier error: {type(e).__name__}: {e}")

    def _build_multileg(self, rec: Record) -> dict:
        """spec: vertical / fly / condor as ONE atomic order.
        Tradier uses 0-INDEXED leg fields (option_symbol[0], side[0]...) and
        REQUIRES a future expiry. Verified live: a 4-leg iron fly places
        atomically and Tradier auto-classifies it (condor)."""
        legs = rec.legs[:4]
        # net credit vs debit decides order type
        net = rec.credit or 0.0
        return {
            "class": "multileg",
            "symbol": rec.underlying,
            "type": "credit" if net > 0 else ("debit" if net < 0 else "market"),
            "price": abs(net),
            "duration": getattr(rec, "duration", "day"),
            "leg_count": len(legs),
            "legs": [
                {"option_symbol": self._occ(l), "side": self._side(l), "quantity": l.qty}
                for l in legs
            ],
            # NOTE: Tradier needs option_symbol[i]/side[i]/quantity[i] with i
            # starting at 0, and a future expiry, or it rejects the order.
        }

    @staticmethod
    def _occ(leg: Leg) -> str:
        # delegate to Leg.occ() — correct OCC symbology (no space, full expiry)
        return leg.occ()

    @staticmethod
    def _side(leg: Leg) -> str:
        return "buy_to_open" if leg.action == "buy" else "sell_to_open"

    def quote_stock(self, sym):
        """Last/bid price for an equity. -> float | None"""
        if not self.cfg or not sym:
            return None
        import requests
        try:
            r = requests.get(
                f"{self.cfg.base_url}/markets/quotes?symbols={sym}",
                headers={"Authorization": f"Bearer {self.cfg.access_token}",
                         "Accept": "application/json"}, timeout=12)
            q = (r.json().get("quotes") or {}).get("quote") or {}
            for k in ("bid", "last", "close"):
                if q.get(k):
                    return float(q[k])
        except Exception:
            return None
        return None

    def place_stock(self, sym, side, qty):
        """Market equity order. side = buy | sell_short."""
        if not self.cfg:
            return ExecResult(ok=False, venue=self.venue, reason="not configured")
        import requests
        try:
            r = requests.post(
                f"{self.cfg.base_url}/accounts/{self.cfg.account_id}/orders",
                headers={"Authorization": f"Bearer {self.cfg.access_token}",
                         "Accept": "application/json"},
                data={"class": "equity", "symbol": sym, "side": side,
                      "quantity": qty, "type": "market",
                      "duration": getattr(trade, "duration", "day")},
                timeout=40)
            oid = (r.json().get("order") or {}).get("id")
            if oid:
                return ExecResult(ok=True, venue=self.venue,
                                  reason=f"equity {side} {qty} {sym}", order_id=str(oid))
            return ExecResult(ok=False, venue=self.venue,
                              reason=f"HTTP {r.status_code}: {r.text[:80]}")
        except Exception as e:
            return ExecResult(ok=False, venue=self.venue,
                              reason=f"{type(e).__name__}: {e}")

    def close(self, trade: dict, limit_price: Optional[float] = None,
              qty: Optional[int] = None) -> ExecResult:
        """Close a Tradier position with CLOSE sides (buy_to_close/sell_to_close).

        Tradier rejects open sides on legs that cover a position ("must be
        placed as a Buy to Cover"). Legs are reversed from the trade record;
        market by default, a debit limit when limit_price is given."""
        if not self.cfg:
            return ExecResult(ok=False, venue=self.venue,
                              reason="Tradier not configured — needs account_id + access_token")
        import requests
        legs = trade.get("legs") or []
        exp = (trade.get("expiry") or trade.get("opt_expiry") or "").replace("-", "")
        if not exp:
            return ExecResult(ok=False, venue=self.venue, reason="no expiry on trade")
        root = close_root(trade)
        H = {"Authorization": f"Bearer {self.cfg.access_token}",
             "Accept": "application/json"}
        url = f"{self.cfg.base_url}/accounts/{self.cfg.account_id}/orders"
        try:
            if len(legs) <= 1:
                occ = (trade.get("opt_id") or "").replace(" ", "")
                if not occ and legs:
                    # D88: single-leg positions can reach the close with no
                    # opt_id (the entry path does not always set one), which
                    # made the position UNCLOSEABLE - "no option symbol" every
                    # cycle while it sat at risk. Build the OCC from the leg +
                    # expiry instead of refusing.
                    try:
                        _k, _r, _a = legs[0][0], str(legs[0][1])[0].upper(), legs[0][2]
                        occ = (f"{root}{exp}{'C' if _k and _r == 'C' else 'P'}"
                               f"{int(round(float(_k) * 1000)):08d}")
                    except (IndexError, TypeError, ValueError):
                        occ = ""
                if not occ:
                    return ExecResult(ok=False, venue=self.venue, reason="no option symbol")
                side = "sell_to_close" if trade.get("direction") == "long" else "buy_to_close"
                data = {"class": "option", "symbol": root, "side": side,
                        "quantity": qty or trade.get("qty") or 1,
                        "type": "limit" if limit_price else "market",
                        "duration": getattr(trade, "duration", "day"),
                        "option_symbol": occ}
                if limit_price:
                    data["price"] = round(abs(limit_price), 2)
            else:
                data = {"class": "multileg", "symbol": root,
                        "type": "debit" if limit_price else "market",
                        "price": round(abs(limit_price or 0), 2),
                        "duration": getattr(trade, "duration", "day"),
                        "quantity": qty or 1}
                for i, leg in enumerate(legs[:4]):
                    strike, right, action = float(leg[0]), str(leg[1]), str(leg[2])
                    data[f"option_symbol[{i}]"] = (
                        f"{root}{exp[2:]}{right[0].upper()}{int(round(strike * 1000)):08d}")
                    data[f"side[{i}]"] = ("buy_to_close" if action == "sell"
                                          else "sell_to_close")
                    # the close amount is the whole remaining position, NOT one
                    # leg. A hardcoded 1 here closed one spread out of TL4003's
                    # five and left four live at the broker while the book
                    # booked all five closed (PnL overstated 4x).
                    data[f"quantity[{i}]"] = qty or trade.get("qty") or 1
            if data.get("duration") == "gtc":
                data.pop("close_date", None)
            r = requests.post(url, headers=H, data=data, timeout=40)
            try:
                j = r.json()
            except Exception:
                return ExecResult(ok=False, venue=self.venue,
                                  reason=f"Tradier HTTP {r.status_code}: {str(r.text)[:100]}")
            oid = (j.get("order") or {}).get("id")
            if oid:
                return ExecResult(ok=True, order_id=str(oid), venue=self.venue,
                                  reason=f"Tradier close order {oid} placed")
            return ExecResult(ok=False, venue=self.venue,
                              reason=f"Tradier rejected: {str(j)[:120]}")
        except Exception as e:
            return ExecResult(ok=False, venue=self.venue,
                              reason=f"Tradier error: {type(e).__name__}: {e}")

    def cancel(self, oid):
        """Cancel a resting order. -> (ok, reason). Used to kill unfilled
        entries so the book never carries a position that does not exist."""
        if not self.cfg or not oid:
            return False, "no config/oid"
        import requests
        try:
            r = requests.delete(
                f"{self.cfg.base_url}/accounts/{self.cfg.account_id}/orders/{oid}",
                headers={"Authorization": f"Bearer {self.cfg.access_token}",
                         "Accept": "application/json"}, timeout=30)
            if r.status_code // 100 == 2:
                return True, "cancelled"
            return False, f"HTTP {r.status_code}"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    def order_fill(self, oid):
        """(status, abs(avg_fill_price) | None) for an order id; (None, None) on failure."""
        if not self.cfg or not oid:
            return None, None
        import requests
        try:
            r = requests.get(f"{self.cfg.base_url}/accounts/{self.cfg.account_id}/orders/{oid}",
                             headers={"Authorization": f"Bearer {self.cfg.access_token}",
                                      "Accept": "application/json"}, timeout=30)
            o = (r.json() or {}).get("order") or {}
            avg = o.get("avg_fill_price")
            return str(o.get("status") or ""), (abs(float(avg)) if avg else None)
        except Exception:
            return None, None

    def order_quantity(self, oid):
        """D131: the broker's filled quantity for an order — the book's size
        must mirror it (multileg response quantity = legs x spreads)."""
        if not self.cfg or not oid:
            return None
        import requests
        try:
            r = requests.get(f"{self.cfg.base_url}/accounts/{self.cfg.account_id}/orders/{oid}",
                             headers={"Authorization": f"Bearer {self.cfg.access_token}",
                                      "Accept": "application/json"}, timeout=30)
            o = (r.json() or {}).get("order") or {}
            q = o.get("exec_quantity") or o.get("quantity")
            return int(q) if q else None
        except Exception:
            return None

