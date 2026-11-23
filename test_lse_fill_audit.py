"""test_lse_fill_audit.py — fill-audit guards (2026-11-21).

Covers: UTC window maths, archive-first (zero live calls for archived roots),
index-first ordering (golden-band/SPX priority), the hard daily budget,
single-option verdicts (GOOD/DIVERGED/NO_TAPE at the 25% D139 band), spread
CONTEXT rows, and settled/intrinsic fills excluded.

Run: python test_lse_fill_audit.py   (exit 0 = green)
"""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import lse_fill_audit as fa

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


T = tempfile.TemporaryDirectory()
tmp = pathlib.Path(T.name)

# ---- fixtures -------------------------------------------------------------
st = {"closed": {
        # single exit, exact ts
        "O1": {"id": "O1", "msg_id": "4534", "instrument": "WMT",
               "opt_id": "WMT 261120C00128000", "legs": [],
               "exit_ts": "2026-11-20 14:48:31", "exit_price": 1.69,
               "fill_note": "target hit @ fill 1.69"},
        # settled intrinsic — must be EXCLUDED
        "O2": {"id": "O2", "msg_id": "4528", "instrument": "QQQ",
               "opt_id": None, "legs": [[648.0, "put", "sell"]],
               "exit_ts": "2026-11-20 11:30:48", "exit_price": 645.04,
               "fill_note": "expired 2026-11-19 — settled intrinsic vs QQQ 645.04"},
        # spread exit
        "TL": {"id": "TL", "msg_id": "4535", "instrument": "SPXW",
               "opt_id": None,
               "legs": [[7170.0, "put", "sell"], [7155.0, "put", "buy"]],
               "exit_ts": "2026-11-20 15:00:04", "exit_price": 9.0,
               "fill_note": "exit[price] @ fill 9.00"},
      },
      "open": {}}
state_p = tmp / "state.json"
state_p.write_text(json.dumps(st), encoding="utf-8")

sig_p = tmp / "signals.csv"
_HDR = ("ts,event,trade_id,msg_id,instrument,symbol,direction,entry,stop,"
        "targets,qty,order_id,confidence,horizon,status,reason,raw")


def _sigrow(ts, event, trade, msg, inst, reason):
    """Exactly 17 columns so reason never shifts (hand-counted commas bit once)."""
    return ",".join([ts, event, trade, msg, inst, "", "", "", "", "", "",
                     "", "", "", "", reason, ""])


sig_p.write_text(
    _HDR + "\n"
    + _sigrow("2026-11-20 14:46", "ORDER", "O1", "4534", "WMT",
              "entry filled on recheck @ 0.88") + "\n"
    + _sigrow("2026-11-18 10:00", "ORDER", "O9", "4000", "WMT",
              "entry filled on recheck @ 1.10") + "\n"
    + _sigrow("2026-11-20 14:40", "ALERT", "O1", "4534", "WMT",
              "some alert") + "\n",
    encoding="utf-8")

# ---- collect_fills --------------------------------------------------------
# fixed 'now' just after the fills
NOW = fa.datetime.datetime(2026, 11, 20, 20, 0, tzinfo=fa.datetime.timezone.utc).timestamp()
fills = fa.collect_fills(hours=26, now=NOW, state_path=state_p, signals_path=sig_p)
kinds = [(f["kind"], f["msg_id"], f["price"]) for f in fills]
check("collect: exit fill captured (1.69)", ("exit", "4534", 1.69) in kinds, str(kinds))
check("collect: entry recheck fill captured (0.88)", ("entry", "4534", 0.88) in kinds, str(kinds))
check("collect: settled-intrinsic fill excluded",
      not any(f["msg_id"] == "4528" for f in fills), str(kinds))
check("collect: spread exit captured with legs",
      any(f["kind"] == "exit" and f["legs"] for f in fills))
check("collect: stale (48h-old) row excluded by window",
      not any(f["price"] == 1.10 for f in fills))
check("priority: index-family sorts first (golden band / SPX first)",
      fills and fa._is_index(fills[0]["instrument"]),
      str([f["instrument"] for f in fills]))

# ---- score: archive-first, budget, verdicts -------------------------------
def _p(ep, sym, prem):
    return (ep, sym, prem)


T_FILL = fa._epoch("2026-11-20 14:48:31")
tape = {
    # archived SPX-family: spread fill scores WITHOUT any live call
    "SPX": [(_p(T_FILL - 60, "SPXW261120P07170000", 5.9),
             _p(T_FILL - 60, "SPXW261120P07170000", 5.9)[1], 5.9)],
    # archived WMT prints near the single exit
    "WMT": [(_p(T_FILL - 30, "WMT 261120C00128000", 1.72),
             "WMT 261120C00128000", 1.72),
            (_p(T_FILL - 30, "WMT 261120C00128000", 1.72)[0],
             "WMT 261120C00128000", 1.72)],
}
# rebuild properly
tape["SPX"] = [(_p(T_FILL - 60, "SPXW261120P07170000", 5.9)[0],
                "SPXW261120P07170000", 5.9)]
tape["WMT"] = [(_p(T_FILL - 30, "WMT 261120C00128000", 1.72)[0],
                "WMT 261120C00128000", 1.72)]

_FETCHED = []
def _stub_fetch(root, day):
    _FETCHED.append(root)
    return [{"ts": "2026-11-20T14:48:00Z", "symbol": "QQQ 261120P00648000",
             "premium": 1.0}]

budget_p = tmp / "budget.json"

# fill list: archived single (WMT), archived spread (SPX), missing root (QQQ)
f2 = [
    {"ts": "2026-11-20 14:48:31", "epoch": T_FILL, "msg_id": "4534",
     "instrument": "WMT", "kind": "exit", "price": 1.69,
     "occ": "WMT 261120C00128000", "legs": []},
    {"ts": "2026-11-20 15:00:04", "epoch": T_FILL, "msg_id": "4535",
     "instrument": "SPXW", "kind": "exit", "price": 9.0,
     "occ": None, "legs": [[7170.0, "put", "sell"], [7155.0, "put", "buy"]]},
    {"ts": "2026-11-20 18:58:41", "epoch": T_FILL, "msg_id": "4529",
     "instrument": "QQQ", "kind": "exit", "price": 0.95,
     "occ": None, "legs": [[650.0, "put", "sell"], [648.0, "put", "buy"]]},
]
rows = fa.score(f2, cap=8, fetch=_stub_fetch, tape=tape, budget_path=budget_p,
                log=lambda *a: None)
by = {(r["msg_id"], r["kind"]): r for r in rows}
check("score: archived single GOOD (1.69 vs 1.72 tape, within 25%)",
      by[("4534", "exit")]["verdict"] == "GOOD"
      and by[("4534", "exit")]["source"] == "archive",
      str(by[("4534", "exit")]))
check("score: archived spread = CONTEXT, never live-fetched",
      by[("4535", "exit")]["verdict"] == "CONTEXT"
      and by[("4535", "exit")]["source"] == "archive"
      and "SPX" not in _FETCHED, str(_FETCHED))
check("score: missing root live-fetched ONCE (budget token spent)",
      _FETCHED == ["QQQ"], str(_FETCHED))
check("score: live-sourced spread graded CONTEXT",
      by[("4529", "exit")]["verdict"] == "CONTEXT"
      and by[("4529", "exit")]["source"] == "live",
      str(by[("4529", "exit")]))

# verdict math: GOOD vs DIVERGED vs NO_TAPE at the 25% band
t1 = fa._epoch("2026-11-20 14:00:00")
def _single(price, tape_rows, msg="M1"):
    return fa.score([{"ts": "2026-11-20 14:00:00", "epoch": t1, "msg_id": msg,
                      "instrument": "XYZ", "kind": "exit", "price": price,
                      "occ": "XYZ 261120C00100000", "legs": []}],
                    cap=8, fetch=_stub_fetch,
                    tape={"XYZ": tape_rows}, budget_path=budget_p,
                    log=lambda *a: None)[0]
r_ok = _single(1.00, [(t1, "XYZ 261120C00100000", 1.05)], "M2")
check("verdict: within 25% = GOOD", r_ok["verdict"] == "GOOD", str(r_ok))
r_dv = _single(2.00, [(t1, "XYZ 261120C00100000", 1.00)], "M3")
check("verdict: beyond 25% = DIVERGED (fill 2.00 vs tape 1.00)",
      r_dv["verdict"] == "DIVERGED" and "+100%" in r_dv["detail"], str(r_dv))
r_nt = _single(1.00, [(t1, "OTHER 261120C00100000", 1.00)], "M4")
check("verdict: no prints for our OCC = NO_TAPE (honest miss)",
      r_nt["verdict"] == "NO_TAPE", str(r_nt))

# archive frozen OUTSIDE the window -> live retry with fresh prints
_FETCHED.clear()
t_win = fa._epoch("2026-11-20 14:00:00")
f_win = [{"ts": "2026-11-20 14:00:00", "epoch": t_win, "msg_id": "W1",
          "instrument": "DDD", "kind": "exit", "price": 1.0,
          "occ": "DDD 261120C00100000", "legs": []}]
tape_old = {"DDD": [(fa._epoch("2026-11-19 09:00:00"),
                     "DDD 261120C00100000", 5.0)]}


def _fetch_ok(root, day):
    _FETCHED.append(root)
    return [{"ts": "2026-11-20T14:00:00Z", "symbol": "DDD 261120C00100000",
             "premium": 1.1}]


rows_w = fa.score(f_win, cap=8, fetch=_fetch_ok, tape=tape_old,
                  budget_path=tmp / "b2.json", log=lambda *a: None)
check("score: archive frozen outside window -> live retry, graded live",
      _FETCHED == ["DDD"] and rows_w[0]["source"] == "live"
      and rows_w[0]["verdict"] == "GOOD",
      str((rows_w, _FETCHED)))

# ---- budget ---------------------------------------------------------------
b1 = tmp / "b1.json"
def _always_fetch(root, day):
    _FETCHED.append(root)
    return []
fa.score([{"ts": "2026-11-20 14:00:00", "epoch": t1, "msg_id": "B1",
           "instrument": "AAA", "kind": "exit", "price": 1.0,
           "occ": "AAA 261120C00100000", "legs": []},
          {"ts": "2026-11-20 14:01:00", "epoch": t1, "msg_id": "B2",
           "instrument": "BBB", "kind": "exit", "price": 1.0,
           "occ": "BBB 261120C00100000", "legs": []}],
         cap=1, fetch=_always_fetch, tape={"AAA": [], "BBB": []},
         budget_path=b1, log=lambda *a: None)
state_b = json.loads(b1.read_text(encoding="utf-8"))
check("budget: cap 1 -> exactly one live call recorded",
      state_b["calls"] == 1, str(state_b))
rows_b = fa.score([{"ts": "2026-11-20 14:02:00", "epoch": t1, "msg_id": "B3",
                    "instrument": "CCC", "kind": "exit", "price": 1.0,
                    "occ": "CCC 261120C00100000", "legs": []}],
                  cap=1, fetch=_always_fetch, tape={"CCC": []},
                  budget_path=b1, log=lambda *a: None)
check("budget: next run with cap exhausted = BUDGET_SKIPPED (never silent)",
      rows_b and rows_b[0]["verdict"] == "BUDGET_SKIPPED"
      and rows_b[0]["source"] == "budget", str(rows_b))

# ---- source guards --------------------------------------------------------
check("guard: default daily cap is 8 (vs plan's 60/min + 12.5M rows)",
      fa.DEFAULT_CAP == 8)
check("guard: SPX-family maps to the SPX archive root (index-first = 0 calls)",
      fa._root("SPXW") == "SPX" and fa._root("NDX100") == "NDX"
      and fa._is_index("SPXW") and fa._is_index("RUT")
      and not fa._is_index("WMT") and fa._root("WMT") == "WMT")
check("guard: canonicalisation lives in ticker_taxonomy only (drift rule)",
      "_ROOT_MAP" not in pathlib.Path(fa.__file__).read_text(encoding="utf-8"))
check("guard: settled/intrinsic regex excludes phantom settles",
      all(k in fa.collect_fills.__doc__ for k in ("settlements", "excluded")))

# ---- report builder (what the Monday schtask pushes) ----------------------
_md = fa.build_md([
    {"ts": "2026-11-20 14:48:31", "msg_id": "4534", "instrument": "WMT",
     "kind": "exit", "fill": 1.69, "tape_prem": 0.90, "verdict": "DIVERGED",
     "source": "archive", "detail": "fill 1.69 vs tape 0.9 (+88%)"},
    {"ts": "2026-11-20 15:00:04", "msg_id": "4535", "instrument": "SPXW",
     "kind": "exit", "fill": 9.0, "tape_prem": "", "verdict": "CONTEXT",
     "source": "archive", "detail": "package not graded"}])
check("report: counts line present",
      "1 DIVERGED" in _md and "1 CONTEXT" in _md, _md)
check("report: DIVERGED rows are listed with their detail",
      "⚠️ DIVERGED" in _md and "+88%" in _md, _md)
check("report: no lag-hint when something graded",
      "not loaded" not in _md, _md)
_md2 = fa.build_md([{"ts": "2026-11-20 14:46:17", "msg_id": "4534",
                     "instrument": "WMT", "kind": "entry", "fill": 0.88,
                     "tape_prem": "", "verdict": "NO_TAPE", "source": "live",
                     "detail": "no prints"}])
check("report: all-NO_TAPE carries the lag hint",
      "not loaded for these dates" in _md2, _md2)
check("report: --push flag exists for the schtask",
      "--push" in pathlib.Path(fa.__file__).read_text(encoding="utf-8"))

# ---- D141: real-spot anchor breach verdict (Friday's exact geometry) ------
_t_b = fa._epoch("2026-11-20 14:46:11")
# AD17951680300: 15-wide put credit, sold7480; tape spot 7007 (Friday's
# crash) -> bound = min(width, intrinsic+tv) = 15.0; the fill was 30.4.
# Keyed by SPX: _root("SPXW") maps to the SPX archive root (D133 map).
tape_sp = {"SPX": [(_t_b, "SPXW261120P07480000", 300.0, 7007.0)]}
f_br = [{"ts": "2026-11-20 14:46:11", "epoch": _t_b, "msg_id": "4540",
         "instrument": "SPXW", "kind": "exit", "price": 30.4, "occ": None,
         "legs": [[7465.0, "put", "buy"], [7480.0, "put", "sell"]]}]
rows_br = fa.score(f_br, cap=8, fetch=_stub_fetch, tape=tape_sp,
                   budget_path=tmp / "b3.json", log=lambda *a: None)
check("D141: fill above the real-spot bound = ANCHOR_BREACH (30.4 > 15.0)",
      rows_br and rows_br[0]["verdict"] == "ANCHOR_BREACH"
      and "impossible package" in rows_br[0]["detail"]
      and "7007" in rows_br[0]["detail"], str(rows_br))
rows_ok = fa.score([{**f_br[0], "price": 12.0}], cap=8, fetch=_stub_fetch,
                   tape=tape_sp, budget_path=tmp / "b3.json",
                   log=lambda *a: None)
check("D141: fill inside the bound stays CONTEXT",
      rows_ok and rows_ok[0]["verdict"] == "CONTEXT", str(rows_ok))

_md3 = fa.build_md([{"ts": "2026-11-20 14:46:11", "msg_id": "4540",
                     "instrument": "SPXW", "kind": "exit", "fill": 30.4,
                     "tape_prem": 300.0, "verdict": "ANCHOR_BREACH",
                     "source": "archive",
                     "detail": "fill 30.4 > real-spot bound 15.0 @ spot 7007"}])
check("report: ANCHOR_BREACH counted and listed with the verdict name",
      "ANCHOR-BREACH" in _md3 and "⚠️ ANCHOR_BREACH" in _md3, _md3)

# ---- D141 gate: tape-refuted fills leave the graduation sample ------------
# ticker_report is a script: exec it against fixture state + fixture audit
# with stdout captured. The refuted loser (msg 9001) must leave the clean
# sample; the exclusion must be visible in the milestone line.
import contextlib as _ctx
import io as _io
import james_pipe as _jp

st_g = {"closed": {
    "S1": {"id": "S1", "msg_id": "9001", "instrument": "SPXW",
           "pnl": -50.0, "exit_ts": "2026-11-10 15:00:00",
           "fill_note": "exit[price] @ fill 5.0"},
    "S2": {"id": "S2", "msg_id": "9002", "instrument": "SPXW",
           "pnl": 50.0, "exit_ts": "2026-11-11 15:00:00",
           "fill_note": "exit[price] @ fill 5.0"}},
    "open": {}}
state_g = tmp / "state_gate.json"
state_g.write_text(json.dumps(st_g), encoding="utf-8")
audit_g = tmp / "audit_gate.csv"
audit_g.write_text(
    "ts,msg_id,instrument,kind,fill,tape_prem,verdict,source,detail\n"
    "2026-11-10 15:00,9001,SPXW,exit,5.0,1.0,DIVERGED,archive,fill vs tape\n",
    encoding="utf-8")
_old_state, _old_out = _jp.STATE, fa.OUT
_jp.STATE, fa.OUT = state_g, audit_g
_buf = _io.StringIO()
try:
    with _ctx.redirect_stdout(_buf):
        _src_p = pathlib.Path(__file__).resolve().parent / "ticker_report.py"
        exec(compile(_src_p.read_text(encoding="utf-8"), str(_src_p), "exec"),
             {"__name__": "__main__", "__file__": str(_src_p)})
finally:
    _jp.STATE, fa.OUT = _old_state, _old_out
_gate_out = _buf.getvalue()
check("D141 gate: tape-refuted loser leaves the clean sample (1/1 not 1/2)",
      "1/1 = 100%" in _gate_out, _gate_out[-300:])
check("D141 gate: exclusion is printed, never silent",
      "1 tape-refuted" in _gate_out and "D141" in _gate_out,
      _gate_out[-300:])

T.cleanup()
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
