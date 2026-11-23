#!/usr/bin/env python3
"""uw_flow.py — FREE Unusual Whales net-premium monitor (no subscription).

The free ticker overview page publishes, per ticker, the latest session's
option volume, total premium and NET premium. Net premium tells the direction
the money is leaning: negative = more premium sold/leaving on the downside
side, positive = net buying. For our SHORT-premium positions (bull puts,
bear calls) a strongly negative net premium on the underlying is the tape
disagreeing with us — the earliest visible exit warning, and exactly the
signal we did NOT have when WPM bled to its stop.

Scope, honestly: the free tier gives the LATEST session only (no history,
no per-strike side data). That is a snapshot heuristic, not the real-time
per-print flow a paid plan gives. It is free, so we run it and log it; the
paid trial is only worth buying if this proves informative.

  python uw_flow.py                 # snapshot every OPEN position's underlying
  python uw_flow.py WPM QQQ SPX     # snapshot specific tickers
  python uw_flow.py --report        # read uw_flow.csv back

Writes append-only uw_flow.csv. Never trades, never blocks the pipe.
"""
import argparse
import csv
import pathlib
import re
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "uw_flow.csv"
COLS = ["ts", "ticker", "session", "volume", "premium", "net_premium", "source"]

# "11/12/2026: Vol: 1,614 Prem: $883,483 Net Prem:-$14,328"
BLOCK = re.compile(
    r"(\d{2}/\d{2}/\d{4}):\s*Vol:\s*([\d,]+)\s*Prem:\s*\$([\d,]+)"
    r"\s*Net Prem:\s*(-?)\$([\d,]+)")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _num(s):
    return int(str(s).replace(",", "") or 0)


def fetch(ticker, timeout=90):
    """Daily history rows from the free overview page TABLE:
        | 11/12 | open | high | low | close | %chg | P/C | volume | ... | net prem | total prem |
    Returns [(session, close, pcr, net_premium)] newest first, or None.
    File-backed output + hard timeout (D57: a child holding a pipe can hang us)."""
    url = f"https://unusualwhales.com/stock/{ticker}/overview"
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8",
                                errors="replace") as of,          tempfile.TemporaryFile(mode="w+", encoding="utf-8",
                                errors="replace") as ef:
        try:
            subprocess.run(["agent-reach", "read", url],
                           stdout=of, stderr=ef, timeout=timeout,
                           cwd=str(HERE))
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None
        of.seek(0)
        raw = of.read()
    text = raw
    try:
        import json
        d = json.loads(raw)
        text = (d.get("items") or [{}])[0].get("text") or ""
    except Exception:
        pass
    out = []
    for line in text.splitlines():
        if not re.search(r"\|\s*\d{1,2}/\d{1,2}\s*\|", line):
            continue
        c = [x.strip() for x in line.split("|") if x.strip()]
        if len(c) < 8:
            continue
        try:
            close = float(c[4].replace("$", "").replace(",", ""))
            pcr = float(c[6]) if c[6] not in ("-", "") else None
            net = int(c[-2].replace("$", "").replace(",", ""))
        except (ValueError, IndexError):
            continue
        out.append((c[0], close, pcr, net))
    return out or None


def snapshot(tickers, log=print, sessions=5):
    """One row per (ticker, session) from the free overview table. Returns
    {ticker: (latest_session, latest_net, series)} where series is newest-first
    [(session, close, pcr, net)] — enough to see which way the money turned."""
    rows = []
    got = {}
    for t in tickers:
        t = str(t).upper().strip()
        if not t:
            continue
        hist = fetch(t)
        if not hist:
            log(f"uw: no data for {t}")
            continue
        series = hist[:sessions]
        got[t] = series
        for session, close, pcr, net in series:
            rows.append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                         "ticker": t, "session": session,
                         "volume": "", "premium": "",
                         "net_premium": net,
                         "source": f"free-overview|close={close:.2f}|pcr={pcr}"})
        head = series[0]
        log(f"uw: {t} {head[0]} close={head[1]:,.2f} pcr={head[2]} "
            f"net=${head[3]:+,}  ({len(series)} sessions)")
    if rows:
        new = not OUT.exists()
        with OUT.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLS)
            if new:
                w.writeheader()
            w.writerows(rows)
    return got


def underlyings_of(st):
    """The underlyings we actually hold (open positions), mapped to their quote
    root so the UW page resolves (SPXW -> SPX)."""
    seen = []
    for t in (st.get("open") or {}).values():
        u = str(t.get("instrument") or "").upper().split()[0]
        try:
            import ticker_taxonomy as _tx
            u = _tx.canonical_of(u) or u
        except Exception:
            pass
        if u and u not in seen:
            seen.append(u)
    return seen


def flag_line(st, got):
    """Tape read per held underlying. For a SHORT-premium position, net
    premium turning negative (and staying there) is the tape leaning against
    us — the earliest exit warning we never had on WPM."""
    out = []
    for t in (st.get("open") or {}).values():
        u = str(t.get("instrument") or "").upper().split()[0]
        try:
            import ticker_taxonomy as _tx
            u = _tx.canonical_of(u) or u
        except Exception:
            pass
        series = got.get(u)
        if not series:
            continue
        latest = series[0]
        nets = [s[3] for s in series]
        neg = sum(1 for n in nets if n < 0)
        side = "SHORT premium" if str(t.get("direction")) == "short" else "long"
        lean = "AGAINST us" if latest[3] < 0 else "with us"
        out.append(f"{t.get('id')} {u} {side}: {latest[0]} net ${latest[3]:+,} "
                   f"({neg}/{len(nets)} sessions negative) -> {lean}")
    return out


def report():
    if not OUT.exists():
        print("no uw_flow.csv yet — run: python uw_flow.py")
        return 0
    rows = list(csv.DictReader(OUT.open(encoding="utf-8")))
    if not rows:
        print("uw_flow.csv is empty")
        return 0
    by = {}
    for r in rows:
        by.setdefault(r["ticker"], []).append(r)
    print(f"=== UW NET PREMIUM ({len(rows)} snapshots, {len(by)} tickers) ===")
    for tk, rs in sorted(by.items()):
        last = rs[-1]
        print(f"  {tk:<6} {last['session']:<10} net ${int(last['net_premium']):>+14,}"
              f"  ({len(rs)} snapshots)")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="*")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--from-state", action="store_true",
                    help="snapshot every open position's underlying")
    a = ap.parse_args()
    if a.report:
        return report()
    tickers = a.tickers
    if a.from_state or not tickers:
        try:
            import json
            st = json.loads((HERE / "state.json").read_text(encoding="utf-8"))
            tickers = underlyings_of(st)
        except Exception as e:
            print(f"cannot read state.json: {e}")
            return 1
    if not tickers:
        print("nothing to snapshot (no open positions)")
        return 0
    got = snapshot(tickers)
    if got:
        print()
        print("tape read:")
        for tk, series in got.items():
            nets = [s[3] for s in series]
            neg = sum(1 for n in nets if n < 0)
            print("  %-6s latest %s net $%+d  (%d/%d sessions negative)"
                  % (tk, series[0][0], series[0][3], neg, len(nets)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
