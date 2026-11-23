#!/usr/bin/env python3
"""lse_flow.py — deep options-flow history with greeks (LSE Data 3.0).

UW's free tier gives a daily net-premium aggregate; its per-print flow is
paid. LSE's free plan exposes the FULL OPRA tape - premium, size, strike,
put/call, IV and every greek, per print, by underlying and date range. That
is the falsifiable post-mortem data we wanted (WPM: did the tape turn against
the 140/135 bull puts while we held them?).

The division of labour (2026-11-18):
  UW free overview   -> daily net-premium snapshot (already built, uw_flow.py)
  LSE options_flow   -> per-print history + greeks (this file)
  UW paid            -> real-time sweep/aggressor flags (NOT needed yet)

Advisory + archive only: writes lse_flow.csv, never trades, never blocks.

  python lse_flow.py WPM 2026-11-05 2026-11-14    # pull a window
  python lse_flow.py --held                        # every open underlying
  python lse_flow.py --report                      # read the archive back
"""
import argparse
import csv
import datetime
import pathlib
import re
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
OUT = HERE / "lse_flow.csv"
COLS = ["ts", "underlying", "print_ts", "premium", "size", "strike",
        "contract_type", "dte", "expiry", "iv", "delta", "gamma", "theta",
        "vega", "underlying_price", "venue", "symbol"]


def _key():
    env = (HERE / ".lse_key").read_text(encoding="utf-8").strip() \
        if (HERE / ".lse_key").exists() else ""
    if env:
        return env
    try:
        hermes_env = pathlib.Path(
            __import__("os").environ.get("LOCALAPPDATA", "")) / "hermes/.env"
        m = re.search(r"LSE_API_KEY=(.+)",
                      hermes_env.read_text(encoding="utf-8", errors="replace"))
        return m.group(1).strip().strip('"').strip("'") if m else ""
    except Exception:
        return ""


def fetch(underlying, start, end, min_premium=5000, limit=5000):
    """Per-print options flow for one underlying over [start, end)."""
    import lse
    key = _key()
    if not key:
        raise RuntimeError("no LSE_API_KEY found in hermes .env")
    c = lse.LSE(key)
    return c.options_flow(underlying=underlying, start=start, end=end,
                          min_premium=min_premium, limit=limit)


def snapshot(underlying, start, end, min_premium=5000, log=print):
    rows = fetch(underlying, start, end, min_premium)
    if not rows:
        log(f"lse: no flow for {underlying} {start}..{end}")
        return []
    out = []
    for r in rows:
        out.append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "underlying": underlying,
                    "print_ts": r.get("ts"), "premium": r.get("premium"),
                    "size": r.get("size"), "strike": r.get("strike"),
                    "contract_type": r.get("contract_type"), "dte": r.get("dte"),
                    "expiry": r.get("expiry"), "iv": r.get("iv"),
                    "delta": r.get("delta"), "gamma": r.get("gamma"),
                    "theta": r.get("theta"), "vega": r.get("vega"),
                    "underlying_price": r.get("underlying_price"),
                    "venue": r.get("venue"), "symbol": r.get("symbol")})
    new = not OUT.exists()
    with OUT.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        if new:
            w.writeheader()
        w.writerows(out)
    log(f"lse: {underlying} {start}..{end} -> {len(out)} print(s)")
    return out


def held_underlyings():
    import json
    try:
        st = json.loads((HERE / "state.json").read_text(encoding="utf-8"))
    except Exception:
        return []
    seen = []
    for t in (st.get("open") or {}).values():
        u = str(t.get("instrument") or "").upper().split()[0]
        # D114: canonicalise via the shared taxonomy, not a local map
        try:
            import ticker_taxonomy as _tx
            u = _tx.canonical_of(u) or u
        except Exception:
            pass
        if u and u not in seen:
            seen.append(u)
    return seen


def report():
    if not OUT.exists():
        print("no lse_flow.csv — run: python lse_flow.py <TICKER> <START> <END>")
        return 0
    rows = list(csv.DictReader(OUT.open(encoding="utf-8")))
    if not rows:
        print("lse_flow.csv is empty")
        return 0
    by = {}
    for r in rows:
        by.setdefault(r["underlying"], []).append(r)
    print(f"=== LSE OPTIONS FLOW ({len(rows)} prints, "
          f"{len(by)} underlying(s)) ===")
    for u, rs in sorted(by.items()):
        prem = sum(float(r["premium"] or 0) for r in rs)
        neg = sum(1 for r in rs if (r.get("contract_type") or "").lower() == "put")
        pos = sum(1 for r in rs if (r.get("contract_type") or "").lower() == "call")
        print(f"  {u:<8} {len(rs):>5} prints  premium ${prem:>14,.0f}  "
              f"{pos}C/{neg}P")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("underlying", nargs="?")
    ap.add_argument("start", nargs="?")
    ap.add_argument("end", nargs="?")
    ap.add_argument("--min-premium", type=float, default=5000)
    ap.add_argument("--held", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        return report()
    if a.held:
        # D108: US-session anchor (host runs NZDT, a day ahead of ET).
        from zoneinfo import ZoneInfo
        _end = datetime.datetime.now(ZoneInfo("America/New_York")).date()
        _start = (_end - datetime.timedelta(days=14)).isoformat()
        for u in held_underlyings():
            snapshot(u, _start, _end.isoformat(), a.min_premium)
        return 0
    if not (a.underlying and a.start and a.end):
        print(__doc__)
        return 0
    snapshot(a.underlying, a.start, a.end, a.min_premium)
    return 0


if __name__ == "__main__":
    sys.exit(main())
