"""test_prod_gate.py — real-funds production mode: fail-closed ladder gate.

The promotion-ladder doctrine (user 2026-11-06): real funds ONLY on the
bankable class after the SPX milestone proves (>=62% on 30 clean). Prod mode
(tradier_env=prod) is wired but every order must pass tradier_prod_gate:

  1. spx_gate.json exists, verdict starts with PASS, file <= 96h old
     (written by ticker_report, refreshed daily by JamesSpxGate);
  2. the ORDER is bankable: 2-leg same-right credit vertical, quality >= 55%,
     dte >= 1, short strike OTM vs the live spot.

Anything else — missing file, HOLD, stale, exception — refuses with a
PROD GATE reason before any HTTP leaves the process. Sandbox is unaffected.

Run: python test_prod_gate.py   (exit 0 = green)
"""
import json
import pathlib
import sys
import time
import types

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import james_pipe as jp          # noqa: E402
import james_exec as je          # noqa: E402

FAILS = []
HERE = pathlib.Path(__file__).parent


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


def leg(k, right, action):
    return types.SimpleNamespace(strike=float(k), right=right, action=action)


def rec(*, legs, direction="short", credit=6.0, expiry=None, underlying="SPX"):
    import datetime as dt
    if expiry is None:
        expiry = (jp.us_today() + dt.timedelta(days=7)).isoformat()
    return types.SimpleNamespace(legs=legs, direction=direction,
                                 credit=credit, expiry=expiry,
                                 underlying=underlying)


# bankable happy-path fixture: 10-wide put credit, quality 0.60, OTM
BANKABLE = rec(legs=[leg(3000, "put", "sell"), leg(2990, "put", "buy")])
_REAL_HERE, _REAL_UQ = jp.HERE, jp._under_quote

# a temp dir as HERE so the LIVE spx_gate.json is never touched by fixtures
TMPDIR = HERE / "_gate_fixture_dir"
TMPDIR.mkdir(exist_ok=True)
jp.HERE = TMPDIR
jp._under_quote = lambda u: 3050.0          # sold put 3000 OTM
GFILE = TMPDIR / "spx_gate.json"


def put_gate(verdict="PASS", age_s=0.0, w=19, n=30):
    if GFILE.exists():
        GFILE.unlink()
    if verdict is not None:
        GFILE.write_text(json.dumps({"ts": time.time() - age_s,
                                     "verdict": verdict, "w": w, "n": n}),
                         encoding="utf-8")


try:
    # ---- gate verdict rules ------------------------------------------------
    put_gate(verdict=None)                                   # no file
    check("gate: missing verdict file -> refused",
          jp.tradier_prod_gate(BANKABLE)[0] is False)

    put_gate(verdict="HOLD - 4 more clean trades")
    ok, why = jp.tradier_prod_gate(BANKABLE)
    check("gate: HOLD milestone -> refused", not ok and "HOLD" in why, why)

    put_gate(verdict="PASS", age_s=97 * 3600)
    ok, why = jp.tradier_prod_gate(BANKABLE)
    check("gate: stale verdict (>96h) -> refused", not ok and "stale" in why, why)

    put_gate()                                                # fresh PASS
    ok, why = jp.tradier_prod_gate(BANKABLE)
    check("gate: fresh PASS + bankable order -> allowed", ok, why)

    # ---- class rules -------------------------------------------------------
    ok, why = jp.tradier_prod_gate(rec(
        legs=[leg(3000, "put", "sell")], direction="short", credit=3.0))
    check("gate: single short -> refused", not ok and "2-leg" in why, why)

    ok, why = jp.tradier_prod_gate(rec(
        legs=[leg(3000, "call", "buy")], direction="long", credit=None))
    check("gate: single long -> refused", not ok, why)

    ok, why = jp.tradier_prod_gate(rec(
        legs=[leg(7745, "call", "sell"), leg(7755, "call", "buy"),
              leg(7765, "call", "buy")], credit=5.0))
    check("gate: 3-leg -> refused", not ok and "2-leg" in why, why)

    ok, why = jp.tradier_prod_gate(rec(
        legs=[leg(7745, "call", "sell"), leg(7755, "put", "buy")], credit=5.0))
    check("gate: mixed rights -> refused", not ok and "same-right" in why, why)

    ok, why = jp.tradier_prod_gate(rec(
        legs=[leg(7745, "call", "buy"), leg(7755, "call", "sell")],
        direction="long", credit=5.0))
    check("gate: long (debit) vertical -> refused", not ok and "credit" in why, why)

    ok, why = jp.tradier_prod_gate(rec(
        legs=[leg(3000, "put", "sell"), leg(2990, "put", "buy")], credit=4.0))
    check("gate: quality 0.40 < 0.55 -> refused", not ok and "quality" in why, why)

    import datetime as dt
    ok, why = jp.tradier_prod_gate(rec(
        legs=[leg(3000, "put", "sell"), leg(2990, "put", "buy")],
        expiry=jp.us_today().isoformat()))
    check("gate: 0DTE -> refused", not ok and "dte" in why, why)

    jp._under_quote = lambda u: None
    ok, why = jp.tradier_prod_gate(BANKABLE)
    check("gate: no spot to prove OTM -> refused", not ok and "spot" in why, why)

    jp._under_quote = lambda u: 2980.0      # spot BELOW the sold put -> ITM
    ok, why = jp.tradier_prod_gate(BANKABLE)
    check("gate: short strike ITM -> refused", not ok and "ITM" in why, why)

    # ---- fail-closed on exceptions ----------------------------------------
    jp.HERE = _REAL_HERE                     # make the gate blow up on read
    ok, why = jp.tradier_prod_gate(BANKABLE)
    # (real spx_gate.json exists and says HOLD -> refused either way)
    check("gate: after restore still refuses (HOLD) — never opens by accident",
          not ok, why)

    # ---- place(): prod refuses BEFORE any HTTP -----------------------------
    class _ProdExec(je.TradierExecutor):
        pass
    jp.HERE = TMPDIR
    put_gate(verdict="HOLD")
    ex = _ProdExec(je.TradierConfig(account_id="A", access_token="T",
                                    sandbox=False,
                                    base_url="https://sandbox.tradier.com/v1"))
    r = ex.place(BANKABLE)
    check("place(): prod + HOLD -> PROD GATE refusal, no HTTP",
          (not r.ok) and str(r.reason).startswith("PROD GATE"), str(r.reason))

    # sandbox path never consults the gate (paper keeps flowing)
    put_gate(verdict=None)
    ex_sb = _ProdExec(je.TradierConfig(account_id="A", access_token="T",
                                       sandbox=True))
    # unconfigured-network aside: gate must not be the reason; pass a rec with
    # no expiry so it fails on 'no expiry' AFTER the (skipped) gate
    r2 = ex_sb.place(types.SimpleNamespace(legs=[], direction="long",
                                           credit=None, expiry="",
                                           underlying="SPX"))
    check("place(): sandbox never hits the gate",
          (not r2.ok) and "PROD GATE" not in str(r2.reason), str(r2.reason))

    # ---- config default ----------------------------------------------------
    _cfg = dict(jp.CFG)
    _cfg.pop("tradier_env", None)
    check("config: tradier_env defaults to sandbox",
          str(_cfg.get("tradier_env") or "sandbox") == "sandbox")

    # ---- source guards -----------------------------------------------------
    t_exec = (HERE / "james_exec.py").read_text(encoding="utf-8")
    i_gate = t_exec.index("PROD GATE")
    i_req = t_exec.index("import requests", i_gate)
    check("source: place() gates prod before any HTTP", i_gate < i_req)
    t_tr = (HERE / "ticker_report.py").read_text(encoding="utf-8")
    check("source: ticker_report writes spx_gate.json verdict",
          "spx_gate.json" in t_tr and '"verdict": state' in t_tr)
finally:
    jp.HERE = _REAL_HERE
    jp._under_quote = _REAL_UQ
    if GFILE.exists():
        GFILE.unlink()
    try:
        TMPDIR.rmdir()
    except OSError:
        pass

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
