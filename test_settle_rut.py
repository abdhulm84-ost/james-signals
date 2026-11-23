"""test_settle_rut.py — D142: RUT parks must settle (LSE tape fallback).

The RUT park (msg 4540) sat unsettled because of two stacked holes:
  1. no official close exists anywhere in our chain — Tradier serves NO RUT
     history (EMPTY probe 2026-11-21) and BlackBull's RUT print is the D133
     garbage (an SPX level);
  2. park_outcome's cache stored the first None FOREVER (`key not in`), so
     the documented "print not up yet — retry later" could never retry.

Fixes: _expiry_close falls back to LSE's REAL option prints for the RUT
family (verified live: tape close 2,581.79 on 11-20, cross-checked by IWM
256.61 x 10.06 — a guessed x10.2 proxy would have been 35pts off), records
the source in the verdict, and a cached None now re-fetches.

Run: python test_settle_rut.py   (exit 0 = green)
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import james_pipe as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


ROW = {"ts": "2026-11-20 15:13", "msg_id": "4540T", "underlying": "RUTW",
       "structure": "", "body": "", "wing_low": "", "wing_high": "",
       "width": "", "reason": "thin cushion, parked",
       "raw": "Sell RUT 2665P n Buy 2655P n collect $440 for today"}

# seams (every network edge stubbed — the suite is offline)
_o_td, _o_lse = jp._tradier_daily_close, jp._lse_index_close
_c0, _s0, _t0 = dict(jp._PX_CACHE), dict(jp._CLOSE_SRC), dict(jp._LSE_TRIED)
jp._PX_CACHE.clear()
jp._CLOSE_SRC.clear()
jp._LSE_TRIED.clear()

try:
    # ---- sticky cache: the first None must NOT poison the retry -----------
    fetches = {"n": 0}

    def _td_none(s, d):
        fetches["n"] += 1
        return None

    jp._tradier_daily_close = _td_none
    jp._lse_index_close = lambda s, d: None
    o1 = jp.park_outcome(dict(ROW))
    o2 = jp.park_outcome(dict(ROW))
    check("D142: unresolved print returns None (retry later)",
          o1 is None, str(o1))
    check("D142: cached None RE-FETCHES (old bug: first None was forever)",
          fetches["n"] >= 2, str(fetches))

    # ---- LSE tape fallback settles the real 4540 shape --------------------
    def _lse_stub(s, d):
        jp._CLOSE_SRC[(s, d)] = "LSE tape"
        return 2581.79

    jp._lse_index_close = _lse_stub
    o3 = jp.park_outcome(dict(ROW))
    check("D142: RUT settles from the LSE tape fallback",
          o3 is not None and o3["settle_px"] == 2581.79,
          str(o3 and o3.get("settle_px")))
    check("D142: verdict carries the source (provenance never silent)",
          o3 and o3["verdict"] == "would have lost (LSE tape close)",
          str(o3 and o3["verdict"]))
    check("D142: 4540 math = max loss -560 (4.40 credit, 10-wide, deep ITM)",
          o3 and o3["would_pnl"] == -560.0, str(o3 and o3.get("would_pnl")))

    # ---- official close wins when it exists (no LSE call, no suffix) -------
    jp._PX_CACHE.clear()
    jp._CLOSE_SRC.clear()
    lse_hits = {"n": 0}

    def _lse_boom(s, d):
        lse_hits["n"] += 1
        return 999.0

    jp._tradier_daily_close = lambda s, d: 2600.0
    jp._lse_index_close = _lse_boom
    o4 = jp.park_outcome(dict(ROW))
    check("D142: official close takes precedence (LSE never called)",
          o4 is not None and o4["settle_px"] == 2600.0 and lse_hits["n"] == 0,
          str((o4 and o4.get("settle_px"), lse_hits)))
    check("D142: official verdict has no source suffix",
          o4 and o4["verdict"] == "would have lost",
          str(o4 and o4["verdict"]))

    # ---- attempt cap: a missing date can never become a call storm --------
    jp._tradier_daily_close = lambda s, d: None
    jp._lse_index_close = _o_lse          # the REAL one (cap check is first)
    jp._LSE_TRIED[("RUT", "2026-11-01")] = 5
    r_cap = jp._lse_index_close("RUT", "2026-11-01")
    check("D142: attempt cap — 5th failure gives up without a call",
          r_cap is None and jp._LSE_TRIED[("RUT", "2026-11-01")] == 5,
          str((r_cap, jp._LSE_TRIED.get(("RUT", "2026-11-01")))))

    # ---- all sources empty past the window -> unsettleable, no fiction ----
    jp._PX_CACHE.clear()
    jp._CLOSE_SRC.clear()
    jp._tradier_daily_close = lambda s, d: None
    jp._lse_index_close = lambda s, d: None
    o5 = jp.park_outcome(dict(ROW, ts="2026-11-16 10:00", msg_id="4335T"))
    check("D142: nothing anywhere past 3 days -> unsettleable (no fiction)",
          o5 is not None and o5["verdict"] == "unsettleable: no close print",
          str(o5 and o5.get("verdict")))
finally:
    jp._tradier_daily_close, jp._lse_index_close = _o_td, _o_lse
    jp._PX_CACHE.clear()
    jp._PX_CACHE.update(_c0)
    jp._CLOSE_SRC.clear()
    jp._CLOSE_SRC.update(_s0)
    jp._LSE_TRIED.clear()
    jp._LSE_TRIED.update(_t0)

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
