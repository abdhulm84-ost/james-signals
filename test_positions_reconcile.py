"""test_positions_reconcile.py — book-vs-broker drift guard.
Run: python test_positions_reconcile.py   (exit 0 = green)
"""
import json
import james_pipe as jp
import reconcile_positions as rp

FAILS = []
PUSH = []
BROKER = {}
DISK = {}


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


def base_state():
    return {"open": {
        "SPREAD1": {"id": "SPREAD1", "instrument": "SPXW", "direction": "short",
                    "qty": 2, "executor": "tradier", "status": "open_tradier",
                    "opt_underlying": "SPXW", "expiry": "2026-11-20",
                    "entry": 5.0, "fill_note": "filled", "stop": 4.0, "targets": [],
                    "legs": [(7600.0, "put", "sell"), (7590.0, "put", "buy")]},
        "PHANTOM1": {"id": "PHANTOM1", "instrument": "BLK", "direction": "short",
                     "qty": 1, "executor": "tradier", "status": "close_pending",
                     "opt_underlying": "BLK", "expiry": "2026-11-06", "entry": 4.8,
                     "fill_note": "entry order expired — no position", "stop": 3.0, "targets": [],
                     "opt_id": "BLK 261106P01185000", "legs": []},
        "FILLEDPH": {"id": "FILLEDPH", "instrument": "XYZ", "direction": "long",
                     "qty": 1, "executor": "tradier", "status": "open_tradier",
                     "opt_underlying": "XYZ", "expiry": "2026-11-20", "entry": 1.0,
                     "fill_note": "filled @ 1.0", "stop": 0.9, "targets": [], "opt_id": "XYZ 261120C00100000",
                     "legs": []}},
        "closed": {}}


jp.save_state = lambda st: DISK.update(st)
jp.push = lambda t, s: PUSH.append(t)
jp.ledger_row = lambda r: None
rp.append_csv = lambda rows: None
rp.market_open = lambda: True
SRC = {}
jp.load = lambda p, d: json.loads(json.dumps(SRC))
rp.broker_positions = lambda: BROKER


def reset(with_phantom=True):
    PUSH.clear()
    DISK.clear()
    BROKER.clear()
    SRC.clear()
    SRC.update(base_state())
    if not with_phantom:
        del SRC["open"]["PHANTOM1"]


# --- 1. clean book/broker -> no findings ---------------------------------
reset(with_phantom=False)
BROKER.update({"SPXW261120P07600000": (-2, -1000.0), "SPXW261120P07590000": (2, 900.0),
               "XYZ261120C00100000": (1, 100.0)})
rc = rp.main()
check("clean compare stays quiet", rc == 0 and not PUSH, str(PUSH))

# --- 2. entry never filled -> phantom auto-voided ------------------------
reset()
BROKER.update({"SPXW261120P07600000": (-2, -1000.0), "SPXW261120P07590000": (2, 900.0),
               "XYZ261120C00100000": (1, 100.0)})       # BLK absent
pre = base_state()["open"]["PHANTOM1"]
rc = rp.main()
check("terminal-unfilled phantom VOIDED at 0",
      "PHANTOM1" in DISK.get("closed", {}) and DISK["closed"]["PHANTOM1"]["pnl"] == 0.0,
      str(DISK.get("closed", {}).get("PHANTOM1")))
check("voided phantom leaves the open book", "PHANTOM1" not in DISK.get("open", {}))
check("void is reported", any("VOIDED" in p for p in PUSH), str(PUSH))

# --- 3. filled trade missing at broker -> PHANTOM, not voided ------------
reset()
BROKER.update({"SPXW261120P07600000": (-2, -1000.0), "SPXW261120P07590000": (2, 900.0)})   # XYZ absent
rp.main()
check("filled position missing -> flagged PHANTOM, still open",
      "FILLEDPH" in DISK.get("open", {}) and any("PHANTOM" in p for p in PUSH), str(PUSH))

# --- 4. unparsable broker position -> UNBOOKED --------------------------
reset()
BROKER.update({"SPXW261120P07600000": (-2, -1000.0), "SPXW261120P07590000": (2, 900.0),
               "XYZ261120C00100000": (1, 100.0), "AAPL": (4, 400.0)})
rp.main()
check("stray stock position reported UNBOOKED",
      any("UNBOOKED AAPL" in p for p in PUSH), str(PUSH))

# --- 4b. parsable orphan leg -> ADOPTED, not left untracked -------------
reset()
BROKER.update({"SPXW261120P07600000": (-2, -1000.0), "SPXW261120P07590000": (2, 900.0),
               "XYZ261120C00100000": (1, 100.0), "HD261218C00350000": (5, 635.0)})
rp.main()
ad = [t for t in DISK.get("open", {}).values() if t.get("instrument") == "HD"]
check("orphan option leg adopted into the book",
      ad and ad[0]["qty"] == 5 and ad[0]["entry"] > 0, str(ad))
check("adoption is announced", any("ADOPTED" in p for p in PUSH), str(PUSH))
check("adopted trade carries the managed schema (stop/targets/...)",
      ad and all(k in ad[0] for k in ("stop", "targets", "confidence", "k",
                                      "exit_price", "exit_ts", "structure")),
      str(sorted(ad[0].keys())) if ad else "no adopt")
# manage must not KeyError on an adopted record
_n = {}
jp.quote_for = lambda t: 1.5
jp._under_quote = lambda u: 100.0
_pushes = len(PUSH)
try:
    jp.manage_once(DISK)
    _ok = True
    _why = ""
except Exception as e:                     # noqa: BLE001
    _ok, _why = False, f"{type(e).__name__}: {e}"
check("manage_once runs over an adopted trade", _ok, _why)

# --- 5. quantity mismatch -> DRIFT --------------------------------------
reset()
BROKER.update({"SPXW261120P07600000": (-1, -500.0), "SPXW261120P07590000": (2, 900.0),
               "XYZ261120C00100000": (1, 100.0)})
rp.main()
check("qty mismatch reported DRIFT",
      any("DRIFT SPXW261120P07600000" in p for p in PUSH), str(PUSH))

# --- 6. market closed -> 2 ---------------------------------------------
rp.market_open = lambda: False
check("market closed exits 2", rp.main() == 2)

print(f"\n{len(FAILS)} FAIL(S)" if FAILS else "\nALL PASS")
raise SystemExit(1 if FAILS else 0)
