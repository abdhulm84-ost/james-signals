"""test_multileg_close_qty.py — a spread close must close the WHOLE position.

TL4003 (WPM 140/135P qty 5) hit its target and the close went out with
quantity[i]=1 on every leg — one spread only. The broker kept 4, but the book
booked all 5 closed, so paper PnL was overstated 4x. Pinned before the fix.
Run: python test_multileg_close_qty.py   (exit 0 = green)
"""
import james_exec as jx

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


class _Cfg:
    base_url = "http://127.0.0.1:1"
    account_id = "x"
    access_token = "y"


ex = jx.TradierExecutor(cfg=_Cfg())


def _patch_post():
    import requests
    def fake(url, headers, data, timeout):
        class R:
            status_code = 200
            def json(self):
                return {"order": {"id": 999}}
        return R()
    setattr(jx._requests if hasattr(jx, "_requests") else requests, "__post_patch__", None)
    import types
    return fake


# capture the data dict the close would send, without hitting the network
import requests
sent = []
_real = requests.post
requests.post = lambda url, headers, data, timeout: (sent.append(data), type("R", (), {
    "status_code": 200, "json": lambda self: {"order": {"id": 1}}})(),)[1]

trade = {"legs": [[140.0, "put", "sell"], [135.0, "put", "buy"]],
         "expiry": "2026-11-20", "symbol": "WPM", "qty": 5, "direction": "short"}
ex.close(trade, qty=5)
check("multileg close sends the full qty per leg",
      sent and all(sent[0].get(f"quantity[{i}]") == 5 for i in range(2)),
      str({k: v for k, v in sent[0].items() if "quantity" in k}))

sent.clear()
ex.close(trade, qty=2)
check("a trim sends the trimmed qty",
      sent and all(sent[0].get(f"quantity[{i}]") == 2 for i in range(2)),
      str({k: v for k, v in sent[0].items() if "quantity" in k}))

requests.post = _real
print(("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
raise SystemExit(0 if not FAILS else 1)
