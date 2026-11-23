"""ticker_report.py — per-underlying win/loss ledger (paper history).

Purpose: find the edge by NAME, not just by structure. For each ticker it shows
the sample, win rate, net, average win/loss, credit/width where measurable, and a
sizing verdict. Nothing is skipped — names are sized up, down or left alone.

Run: python ticker_report.py [days]      (default 60)
"""
import collections
import datetime
import json
import sys

import james_pipe as jp

days = int(sys.argv[1]) if len(sys.argv) > 1 else 60
cut = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
st = json.loads(jp.STATE.read_text(encoding="utf-8"))

rows = collections.defaultdict(list)
DEFECT = ("manual close", "wrong close", "suspected mis-capture", "REJECTED",
          "unfilled", "cancel", "entry order failed")
defects = collections.Counter()
for k, t in st.get("closed", {}).items():
    if t.get("pnl") is None or str(k).startswith("TEST"):
        continue
    if not (t.get("exit_ts") or "")[:10] >= cut:
        continue
    note = str(t.get("fill_note") or "")
    raw0 = (t.get("instrument") or "").split()[0].upper()
    # everything before the 11/04-11/05 close-root/symbol/exit fixes is
    # defect-era, whatever its note says
    if (t.get("exit_ts") or "")[:10] < "2026-11-05"             or t.get("card_entry_far")             or any(d.lower() in note.lower() for d in DEFECT):
        defects[jp._canon_underlying(raw0) or raw0] += 1
        continue                       # pipeline-era rows, not the strategy
    raw = (t.get("instrument") or "").split()[0].upper()
    if not raw:
        continue
    sym = jp._canon_underlying(raw) or raw   # SPXW/SPX500/SPX are one index
    rows[sym].append(t)

def q(t):
    legs = t.get("legs") or []
    ks = [float(l[0]) for l in legs if len(l) > 2]
    if len(ks) < 2 or not t.get("entry"):
        return None
    w = max(ks) - min(ks)
    return (t["entry"] / w) if w else None

print(f"per-ticker ledger — last {days} days (cut {cut})\n")
print(f"{'ticker':6} {'n':>3} {'d':>2} {'W/L':>6} {'win%':>5} {'net':>10} "
      f"{'avg win':>9} {'avg loss':>9} {'c/w':>6}  verdict")
order = sorted(rows.items(), key=lambda kv: sum(x["pnl"] for x in kv[1]))
for sym, ts in order:
    ts = [t for t in ts if t.get("pnl") is not None]
    n = len(ts)
    if not n:
        continue
    wins = [t["pnl"] for t in ts if t["pnl"] > 0]
    losses = [t["pnl"] for t in ts if t["pnl"] < 0]
    net = sum(t["pnl"] for t in ts)
    qs = [x for x in (q(t) for t in ts) if x]
    qavg = f"{sum(qs) / len(qs):.0%}" if qs else "-"
    _aw = (sum(wins) / len(wins)) if wins else 0
    _al = abs(sum(losses) / len(losses)) if losses else 0
    verdict = ("edge — size up" if n >= 10 and net > 0 and _aw > _al
               else "work — half size / refine" if n >= 3 and net < 0
               else "thin sample")
    print(f"{sym:6} {n:>3} {defects.get(sym, 0):>2} {len(wins):>3}/{len(losses):<3} "
          f"{(len(wins) / n * 100):>4.0f}% {net:>10.2f} "
          f"{(sum(wins) / len(wins)) if wins else 0:>9.2f} "
          f"{(sum(losses) / len(losses)) if losses else 0:>9.2f} {qavg:>6}  {verdict}")

print("\nReading it: 'edge' names get more size, 'work' names get less until "
      "their loss pattern is understood, thin samples stay unchanged. Structures "
      "and entry distance are on each row in the ledger for the diagnosis.")


# ---- SPX milestone: >=62% on the last 30 CLEAN SPX trades -----------------
# D141 (2026-11-21): a fill the REAL tape contradicts (lse_fill_audit grades
# DIVERGED / ANCHOR_BREACH) is not clean evidence — it never counts toward
# the gate in either direction. The exclusion is printed, never silent: the
# sample shrinks by an unknown, it never gains a fiction.
try:
    import csv as _csv
    import lse_fill_audit as _fa
    _refuted = set()
    if _fa.OUT.exists():
        with _fa.OUT.open(encoding="utf-8") as _f:
            for _r in _csv.DictReader(_f):
                if _r.get("verdict") in ("DIVERGED", "ANCHOR_BREACH"):
                    _refuted.add(str(_r.get("msg_id") or ""))
except Exception:
    _refuted = set()
# D131/D135 legging-artifact closes (2026-11-20): the three orphan market
# closes booked -32,072 against a true defined-risk bound of -7,941 (two of
# them are SPXW and sat INSIDE the clean sample, dragging it from ~50% to
# 46% and from +$8.2k to -$16.3k). The epoch rule says counting bugs measures
# bugs, not the edge — documented fiction never counts, in either direction.
_ORPHANS = {"AD17951680300", "AD17951376140", "TL4483"}
_cand = [t for _k, t in st.get("closed", {}).items()
         if str(_k) not in _ORPHANS
         and (t.get("instrument") or "").upper().startswith(("SPX", "SPXW"))
         and t.get("pnl") is not None
         and (t.get("exit_ts") or "")[:10] >= "2026-11-05"
         and not t.get("card_entry_far")
         and not any(d.lower() in str(t.get("fill_note") or "").lower() for d in DEFECT)]
_excl = [t for t in _cand if str(t.get("msg_id") or "") in _refuted]
spx = [t for t in _cand if str(t.get("msg_id") or "") not in _refuted]
spx.sort(key=lambda t: t.get("exit_ts") or "")
spx = spx[-30:]
if spx:
    w = sum(1 for t in spx if t["pnl"] > 0)
    rate = w / len(spx) * 100
    need = max(0, 30 - len(spx))
    state = ("PASS" if len(spx) >= 30 and rate >= 62
             else f"HOLD - {need} more clean trades" if len(spx) < 30 else "HOLD")
    _note = (f" — {len(_excl)} tape-refuted fill(s) excluded "
             f"(wrong vs the real tape, D141)" if _excl else "")
    print(f"\nSPX MILESTONE (>=62% on 30 clean): {w}/{len(spx)} = {rate:.0f}% "
          f"{state}{_note}")
    # REAL-FUNDS GATE FILE — prod mode (tradier_env=prod) reads this and
    # fails CLOSED without it; JamesSpxGate refreshes it daily 07:30.
    # D145-class guard: never write the LIVE verdict from a fixture exec
    # (test_lse_fill_audit runs this script against a 1-trade fixture and
    # used to clobber the real file with "HOLD - 29 more").
    try:
        from signal_scorecard import enabled as _sc_enabled
        if _sc_enabled():
            import json as _json
            import time as _time
            import pathlib as _pathlib
            (_pathlib.Path(__file__).resolve().parent / "spx_gate.json").write_text(
                _json.dumps({"ts": _time.time(), "verdict": state,
                             "w": w, "n": len(spx), "rate": round(rate, 1)}),
                encoding="utf-8")
    except Exception:
        pass
else:
    print("\nSPX MILESTONE: no clean trades yet")

print("PHASE 2 (after PASS + revenue): buy Skylit Initiate, overlay dealer "
      "wall/flip on SPX exits, target 75%. Free tier is manual now — log with "
      "skylit_note.py; the comparison decides the purchase.")
