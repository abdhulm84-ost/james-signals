"""lse_fill_audit.py — score our fills against LSE's real tape (2026-11-21).

Answers "was that fill real or the sandbox's invention?" with the outside
witness — LSE per-print tape — the day after the fill.

USER PRIORITY (2026-11-21): spend LSE on golden-band/SPX FIRST (SPX
graduates first — the 30-clean-trade gate) and stay inside the free tier.

QUOTA FACTS (server /v3/usage, queried 2026-11-21): free plan — 60 calls/min
(the client waits out 429 Retry-After itself), 12.5M rows/day (0 used),
12.89 GB/week (0 used). Every options_flow call = 1 HTTP request (pages only
past 5,000 rows). Current live burn: 1-6 flow calls/day (should_lse gate:
RTH = SPX only, never on narrative/exit).

DISCIPLINE (enforced here):
  1. ARCHIVE FIRST — lse_flow.csv (4M+ prints, refilled by `lse_flow.py
     --held`) scores every index fill with ZERO live calls.
  2. Live fetch only for underlyings missing from the archive, ordered
     index-family first (SPX/SPXW/RUT/NDX...), everything else after.
  3. HARD DAILY BUDGET — lse_audit_budget.json counts live calls; default
     cap 8/day. When the budget is gone the run still scores from archive
     and marks the rest BUDGET_SKIPPED — never a silent overrun.

VERDICTS: single-option fills match their exact OCC symbol on the tape within
+/-10 min -> GOOD (fill within 25% of the print — the D139 band) / DIVERGED
(outside it) / NO_TAPE (honest miss). Spread fills have no package price on a
per-print tape — they get a CONTEXT row (tape activity around the fill),
honestly ungraded. Intrinsic settlements (expired/rolled) are not tape fills
and are excluded.

Usage:  python lse_fill_audit.py             # last 26h of fills
        python lse_fill_audit.py --hours 48 --cap 4
"""
import argparse
import csv
import datetime
import json
import pathlib
import re
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from james_core import anchor_close_cap        # D141: pure geometry, no deps

TAPE = HERE / "lse_flow.csv"
SIGNALS = HERE / "signals.csv"
STATE = HERE / "state.json"
OUT = HERE / "lse_fill_audit.csv"
BUDGET = HERE / "lse_audit_budget.json"
COLS = ["ts", "msg_id", "instrument", "kind", "fill", "tape_prem",
        "verdict", "source", "detail"]
DEFAULT_CAP = 8
_INDEX_FAMILY = {"SPX", "SPXW", "SPX500", "RUT", "RUTW", "NDX", "NDX100"}


def _root(inst):
    """Tape root for an instrument: the canonical index family (SPXW/SPX500
    -> SPX, NDX100 -> NDX, RUTW -> RUT). Delegates to ticker_taxonomy —
    ONE definition of ticker canonicalisation (D126/drift-guard rule), not a
    second map living here."""
    u = str(inst or "").upper().split()[0]
    try:
        import ticker_taxonomy as tx
        return (tx.canonical_of(u) or u).upper()
    except Exception:
        return u


def _is_index(inst):
    return str(inst or "").upper().split()[0] in _INDEX_FAMILY


def _epoch(ts):
    """'2026-11-20 15:00:04' (UTC, the pipe's clock) -> epoch seconds.
    Parsed AS UTC: mktime would shift by the host's NZ offset and the
    +/-10min window would never meet the tape's UTC print_ts."""
    s = str(ts)
    for fmt, n in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16)):
        try:
            return datetime.datetime.strptime(
                s[:n], fmt).replace(tzinfo=datetime.timezone.utc).timestamp()
        except ValueError:
            continue
    return None


def _print_epoch(iso):
    """LSE print_ts '2026-11-13T20:41:16.95Z' -> epoch (UTC)."""
    try:
        return datetime.datetime.fromisoformat(
            str(iso).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def collect_fills(hours=26, now=None, state_path=STATE, signals_path=SIGNALS):
    """Exit fills from state (exact exit_ts) + entry fills from signals
    (the 'filled on recheck' row is written AT the fill). Intrinsic
    settlements excluded — they are not tape fills."""
    now = now or time.time()
    cutoff = now - hours * 3600
    fills = []
    st = json.loads(pathlib.Path(state_path).read_text(encoding="utf-8"))
    for bucket in ("closed", "open"):
        for t in (st.get(bucket) or {}).values():
            mid = str(t.get("msg_id") or "")
            legs = t.get("legs") or []
            opt = t.get("opt_id")
            note = str(t.get("fill_note") or "")
            if re.search(r"expired|settled|intrinsic|phantom|voided", note, re.I):
                continue                      # not a tape fill
            # exit fill (exact)
            ex = _epoch(t.get("exit_ts"))
            if ex and ex >= cutoff and t.get("exit_price") is not None:
                fills.append({
                    "ts": t["exit_ts"], "epoch": ex, "msg_id": mid,
                    "instrument": t.get("instrument"), "kind": "exit",
                    "price": float(t["exit_price"]),
                    "occ": (opt if opt else None),
                    "legs": legs if len(legs) >= 2 else [],
                })
            # entry fill (approx ts = signals row written at the fill)
    # entry fills from signals: rows whose reason records a fill
    try:
        rows = csv.DictReader(pathlib.Path(signals_path).open(encoding="utf-8"))
    except FileNotFoundError:
        rows = []
    _by_msg = {}
    for b in ("closed", "open"):
        for t in (st.get(b) or {}).values():
            _by_msg[str(t.get("msg_id") or "")] = t
    for r in rows:
        ex = _epoch(r.get("ts"))
        if ex is None or ex < cutoff:
            continue
        reason = str(r.get("reason") or "")
        m = re.search(r"(?:filled @|recheck @)\s*([0-9]+(?:\.[0-9]+)?)", reason)
        if not m:
            continue
        t = _by_msg.get(str(r.get("msg_id") or ""))
        if t is None:
            continue
        note = str(t.get("fill_note") or "")
        if re.search(r"expired|settled|intrinsic|phantom|voided", note, re.I):
            continue
        fills.append({
            "ts": r.get("ts"), "epoch": ex, "msg_id": str(r.get("msg_id") or ""),
            "instrument": r.get("instrument") or t.get("instrument"),
            "kind": "entry", "price": float(m.group(1)),
            "occ": t.get("opt_id"),
            "legs": (t.get("legs") or []) if len(t.get("legs") or []) >= 2 else [],
        })
    # priority: index-family first (golden band = SPX verticals), then the rest
    fills.sort(key=lambda f: (0 if _is_index(f["instrument"]) else 1,
                              str(f["instrument"] or ""), str(f["ts"] or "")))
    return fills


def _load_tape(roots):
    """Archive rows for the needed roots, one pass. {root: [(epoch, symbol, prem)]}"""
    out = {r: [] for r in roots}
    if not TAPE.exists() or not roots:
        return out
    with TAPE.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            u = str(r.get("underlying") or "").upper().split()[0]
            if u in roots:
                ep = _print_epoch(r.get("print_ts"))
                if ep is not None:
                    try:
                        _sp = float(r.get("underlying_price") or 0) or None
                    except (TypeError, ValueError):
                        _sp = None
                    out[u].append((ep, str(r.get("symbol") or ""),
                                   float(r.get("premium") or 0), _sp))
    return out


def budget_take(cap=DEFAULT_CAP, path=BUDGET):
    """One live-call token for today. Returns True when a call may be made."""
    today = datetime.date.today().isoformat()
    state = {"date": today, "calls": 0}
    if pathlib.Path(path).exists():
        try:
            state = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    if state.get("date") != today:
        state = {"date": today, "calls": 0}
    if int(state.get("calls") or 0) >= cap:
        return False
    state["calls"] = int(state.get("calls") or 0) + 1
    pathlib.Path(path).write_text(json.dumps(state), encoding="utf-8")
    return True


def _live_fetch(root, t0):
    """1 HTTP request for one fill's +/-10min window (budget token must be
    taken first). Window-scoped: the day-level 'newest 5000' truncation
    missed mid-day fills on the first run. min_premium 200 (not 2000): our
    fills are 1-lot retail prints — the default filter dropped WMT's whole
    window. Windows are half-open [start, end) server-side; start != end."""
    import lse
    from lse_flow import _key
    c = lse.LSE(_key())
    start = datetime.datetime.fromtimestamp(
        t0 - 600, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = datetime.datetime.fromtimestamp(
        t0 + 600, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return c.options_flow(underlying=root, start=start, end=end,
                          min_premium=200, limit=5000)


def score(fills, cap=DEFAULT_CAP, fetch=_live_fetch, tape=None,
          budget_path=BUDGET, log=print):
    """Score fills archive-first, index-first, inside the daily budget."""
    roots = sorted({_root(f["instrument"]) for f in fills})
    tape = _load_tape(roots) if tape is None else tape
    live_cache = {}
    rows = []
    for f in fills:
        root = _root(f["instrument"])
        day = str(f["ts"])[:10]
        t0 = f["epoch"]
        # CFD/underlying fills: nothing to fetch — options tape can't grade
        # them, and a fetch would only burn a budget token (NDX lesson).
        if not f.get("occ") and not f["legs"]:
            rows.append({**_row_base(f), "tape_prem": "",
                         "verdict": "NOT_OPTION", "source": "n/a",
                         "detail": "CFD/underlying fill — options tape "
                                   "cannot grade it"})
            continue
        prints = list(tape.get(root) or [])
        source = "archive"
        win = [p for p in prints if p[0] is not None and abs(p[0] - t0) <= 600]
        if not win:
            # archive empty OR frozen outside the window (the tape only holds
            # what --held last pulled): retry live, window-scoped, in budget.
            # NOTE: LSE lags ~1 day (live check 2026-11-21: newest print was
            # 2026-11-19) — a window whose data is not loaded yet returns 0
            # rows no matter what; rerun when the tape catches up.
            if root not in live_cache:
                if not budget_take(cap, budget_path):
                    live_cache[root] = "BUDGET"
                else:
                    try:
                        got = fetch(root, t0)
                        live_cache[root] = [
                            (_print_epoch(g.get("ts")), str(g.get("symbol") or ""),
                             float(g.get("premium") or 0),
                             (float(g.get("underlying_price") or 0) or None))
                            for g in (got or [])]
                        log(f"lse-audit: live fetch {root} -> "
                            f"{len(live_cache[root])} print(s)")
                    except Exception as e:
                        live_cache[root] = []
                        log(f"lse-audit: live fetch {root} failed: "
                            f"{type(e).__name__}: {e}")
            if live_cache[root] == "BUDGET":
                if not prints:             # nothing anywhere -> honest skip
                    rows.append({**_row_base(f), "tape_prem": "",
                                 "verdict": "BUDGET_SKIPPED", "source": "budget",
                                 "detail": f"daily cap {cap} reached"})
                    continue
                # archive exists but not for this window — grade from archive
            else:
                if live_cache[root]:
                    prints = live_cache[root]
                    source = "live"
                win = [p for p in prints
                       if p[0] is not None and abs(p[0] - t0) <= 600]
        if f["legs"]:
            # spread: no package price on a per-print tape — context only,
            # UNLESS the tape carries the real underlying price (D141): a
            # package fill above the real-spot bound is IMPOSSIBLE, not
            # merely odd (Friday's 30.40 on a 15-wide).
            tot = sum(p[2] for p in win)
            _base = {**_row_base(f), "tape_prem": round(tot, 2)}
            _spots = [(abs(p[0] - t0), p[3]) for p in win
                      if len(p) > 3 and p[3]]
            if _spots:
                S = min(_spots)[1]
                _cap = anchor_close_cap(f["legs"], S)
                if _cap is not None and f["price"] > _cap:
                    rows.append({**_base, "verdict": "ANCHOR_BREACH",
                                 "source": source,
                                 "detail": f"fill {f['price']:g} > real-spot "
                                           f"bound {_cap:g} @ spot {S:g} — "
                                           f"impossible package (D140)"})
                    continue
            rows.append({**_base, "verdict": "CONTEXT", "source": source,
                         "detail": f"{len(win)} print(s), ${tot:,.0f} premium "
                                   f"in window (package not graded)"})
            continue
        occ = str(f["occ"]).replace(" ", "").upper()
        same = [p for p in win if p[1].replace(" ", "").upper() == occ]
        if not same:
            rows.append({**_row_base(f), "tape_prem": "",
                         "verdict": "NO_TAPE", "source": source,
                         "detail": f"no prints for {f['occ']} in window"})
            continue
        near = min(same, key=lambda p: abs(p[0] - t0))
        prem = near[2]
        if prem <= 0:
            rows.append({**_row_base(f), "tape_prem": prem,
                         "verdict": "NO_TAPE", "source": source,
                         "detail": "zero-premium print"})
            continue
        d = (f["price"] - prem) / prem
        verdict = "GOOD" if abs(d) <= 0.25 else "DIVERGED"
        rows.append({**_row_base(f), "tape_prem": round(prem, 4),
                     "verdict": verdict, "source": source,
                     "detail": f"fill {f['price']:g} vs tape {prem:g} "
                               f"({d:+.0%})"})
    return rows


def _row_base(f):
    return {"ts": f["ts"], "msg_id": f["msg_id"], "instrument": f["instrument"],
            "kind": f["kind"], "fill": f["price"]}


def build_md(rows, cap=DEFAULT_CAP):
    """Compact Saved-Messages report for the scheduled Monday run."""
    today = datetime.date.today().isoformat()
    n = {k: sum(1 for r in rows if r["verdict"] == k) for k in
         ("GOOD", "DIVERGED", "ANCHOR_BREACH", "CONTEXT", "NO_TAPE",
          "BUDGET_SKIPPED", "NOT_OPTION")}
    lines = [f"📋 LSE FILL AUDIT — {today} (our fills vs the real tape)", "",
             f"Scored {len(rows)}: {n['GOOD']} GOOD / {n['DIVERGED']} DIVERGED "
             f"/ {n['ANCHOR_BREACH']} ANCHOR-BREACH / {n['CONTEXT']} CONTEXT "
             f"/ {n['NO_TAPE']} NO_TAPE "
             f"/ {n['BUDGET_SKIPPED']} budget-skipped", ""]
    for r in rows:
        if r["verdict"] in ("DIVERGED", "ANCHOR_BREACH"):
            lines.append(f"• ⚠️ {r['verdict']} {r['ts']} {r['instrument']} "
                         f"{r['kind']}: {r['detail']}")
    if n["GOOD"] or n["DIVERGED"] or n["ANCHOR_BREACH"]:
        lines.append("")
        lines.append("graded: single-option fills vs the exact OCC print "
                     "(25% band); spreads vs the real-spot bound; "
                     "refuted fills leave the graduation sample (D141)")
    if n["NO_TAPE"] and not n["GOOD"] and not n["DIVERGED"]:
        lines.append("LSE tape not loaded for these dates yet (~1 day lag) "
                     "— rerun lse_fill_audit.py when it catches up.")
    try:
        b = json.loads(BUDGET.read_text(encoding="utf-8"))
        lines.append(f"live calls: {b.get('calls', 0)}/{cap} today "
                     f"(free plan: 60/min, 12.5M rows/day)")
    except (OSError, ValueError):
        pass
    return "\n".join(lines)


def _push(text):
    """Same channel as push_report.py (established schtask-safe pattern)."""
    import time as _time
    import james_pipe as jp
    st = {"alerts_today": 0, "alerts_date": jp.today(), "push_recent": {}}
    jp.push(text, st)
    _time.sleep(8)              # let _push_send's thread finish


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=26)
    ap.add_argument("--cap", type=int, default=DEFAULT_CAP)
    ap.add_argument("--push", action="store_true",
                    help="push the summary to Telegram Saved Messages "
                         "(the scheduled Monday run does this)")
    a = ap.parse_args(argv)
    fills = collect_fills(hours=a.hours)
    if not fills:
        print(f"lse-audit: no fills in the last {a.hours}h")
        if a.push:
            _push(f"📋 LSE FILL AUDIT — {datetime.date.today().isoformat()}: "
                  f"no fills in the last {a.hours}h — nothing to grade.")
        return 0
    rows = score(fills, cap=a.cap)
    new = not OUT.exists()
    with OUT.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        if new:
            w.writeheader()
        w.writerows(rows)
    n_div = sum(1 for r in rows if r["verdict"] in ("DIVERGED", "ANCHOR_BREACH"))
    n_good = sum(1 for r in rows if r["verdict"] == "GOOD")
    n_nt = sum(1 for r in rows if r["verdict"] == "NO_TAPE")
    print(f"lse-audit: {len(rows)} fill(s) scored — {n_good} GOOD, "
          f"{n_div} DIVERGED/BREACH, "
          f"{sum(1 for r in rows if r['verdict'] == 'CONTEXT')} CONTEXT, "
          f"{n_nt} NO_TAPE, "
          f"{sum(1 for r in rows if r['verdict'] == 'BUDGET_SKIPPED')} skipped")
    if n_nt and not n_good and not n_div:
        print("hint: every single fill came back NO_TAPE — LSE lags ~1 day "
              "(newest tape print was 2026-11-19 on 2026-11-21); rerun when "
              "the tape covers the fill dates (usually the next session day).")
    for r in rows:
        print(f"  {r['ts']} {r['instrument']} {r['kind']:5} fill {r['fill']:>7} "
              f"-> {r['verdict']:<15} [{r['source']}] {r['detail']}")
    if a.push:
        _push(build_md(rows, a.cap))
        print("pushed: summary to Saved Messages")
    return 1 if n_div else 0


if __name__ == "__main__":
    raise SystemExit(main())
