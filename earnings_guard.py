#!/usr/bin/env python3
"""earnings_guard.py — the earnings calendar, as a risk gate (2026-11-18).

Why: James trades earnings plays constantly, and they have hurt us. WPM was a
"Bullish for WPM Earnings" bull put with a 15-day expiry — i.e. an exit AFTER
the binary event — and it was stopped for -$960. CHTR lost -$4,900 on an
earnings position. The pattern table shows the earnings shape
(vertical_credit|bullish|3+|fat) at 4W/2L.

Finviz publishes a free, reliable earnings calendar with BMO/AMC timing. That
is the one thing it adds to this pipeline: knowing, WITHOUT relying on James
saying so, that an underlying is about to report.

  python earnings_guard.py --refresh        # pull the next N days -> csv
  python earnings_guard.py --check WPM 2026-11-20   # advisory for one signal
  python earnings_guard.py --report         # what is in the calendar

The check is ADVISORY: it prints a warning and never blocks a trade.
"""
import argparse
import csv
import datetime
import json
import pathlib
import re
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
CAL = HERE / "earnings_calendar.csv"
COLS = ["date", "ticker", "company", "when", "market_cap", "fetched"]

ROW = re.compile(
    r"\|\s*!\[Image[^\]]*\]\([^)]*\)[A-Z]?\[?([A-Z]{1,6})\]?\([^)]*\)\s*"
    r"\|\s*([^|]{1,60})\|\s*(BMO|AMC|TAS|TNS)\s*\|\s*([^|]{0,20})\|")


def _fetch(url, timeout=90):
    """agent-reach read (JS render) with FILE-backed output + hard timeout -
    the D57 rule: a child holding a pipe must never hang the pipeline."""
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8",
                                errors="replace") as of, \
         tempfile.TemporaryFile(mode="w+", encoding="utf-8",
                                errors="replace") as ef:
        try:
            subprocess.run(["agent-reach", "read", url], stdout=of, stderr=ef,
                           timeout=timeout, cwd=str(HERE))
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return ""
        of.seek(0)
        raw = of.read()
    try:
        return (json.loads(raw).get("items") or [{}])[0].get("text") or ""
    except Exception:
        return raw


def refresh(days=7, log=print):
    """Pull the next `days` sessions into earnings_calendar.csv (replace)."""
    out = []
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    d0 = datetime.date.today()
    for i in range(days):
        day = (d0 + datetime.timedelta(days=i)).isoformat()
        txt = _fetch(f"https://finviz.com/calendar/earnings?dateFrom={day}")
        if not txt:
            log(f"earnings: no data for {day}")
            continue
        n = 0
        for m in ROW.finditer(txt):
            tk, comp, when, mcap = m.group(1), m.group(2).strip(), m.group(3), m.group(4).strip()
            out.append({"date": day, "ticker": tk.upper(), "company": comp[:50],
                        "when": when, "market_cap": mcap[:16], "fetched": stamp})
            n += 1
        log(f"earnings: {day} -> {n} ticker(s)")
    # dedupe by (date, ticker)
    seen, rows = set(), []
    for r in out:
        k = (r["date"], r["ticker"])
        if k in seen:
            continue
        seen.add(k)
        rows.append(r)
    with CAL.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    log(f"earnings: {len(rows)} row(s) -> {CAL.name}")
    return rows


def load():
    if not CAL.exists():
        return []
    return list(csv.DictReader(CAL.open(encoding="utf-8")))


def check(ticker, expiry=None, log=print):
    """ADVISORY: is this underlying reporting near the trade's life?
    Returns a one-line string; never blocks, never sizes."""
    t = str(ticker or "").upper().strip()
    if not t:
        return ""
    rows = [r for r in load() if r["ticker"] == t]
    if not rows:
        return ""
    today = datetime.date.today().isoformat()
    rows.sort(key=lambda r: r["date"])
    lines = []
    for r in rows:
        d = r["date"]
        if d < today:
            continue
        when = {"BMO": "before the open", "AMC": "after the close",
                "TAS": "time not supplied", "TNS": "time not supplied"}.get(
                    r["when"], r["when"])
        msg = f"⚠️ EARNINGS: {t} reports {d} {when}"
        if expiry:
            try:
                e = datetime.date.fromisoformat(str(expiry)[:10])
                ed = datetime.date.fromisoformat(d)
                if e >= ed:
                    msg += (f" — this trade expires {expiry}, i.e. it HOLDS "
                            f"THROUGH the event")
                else:
                    msg += f" — expires {expiry}, before the event"
            except (TypeError, ValueError):
                pass
        lines.append(msg)
    if not lines:
        return ""
    return lines[0]


def report():
    rows = load()
    if not rows:
        print("no calendar yet — run: python earnings_guard.py --refresh")
        return 0
    today = datetime.date.today().isoformat()
    rows = [r for r in rows if r["date"] >= today]
    rows.sort(key=lambda r: (r["date"], r["ticker"]))
    print(f"=== EARNINGS CALENDAR ({len(rows)} upcoming) ===")
    for r in rows[:40]:
        print(f"  {r['date']}  {r['ticker']:<6} {r['when']:<4} {r['company'][:34]}")
    if len(rows) > 40:
        print(f"  ... and {len(rows) - 40} more")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--check", nargs=2, metavar=("TICKER", "EXPIRY"), default=None)
    a = ap.parse_args()
    if a.refresh:
        refresh(a.days)
        return 0
    if a.report:
        return report()
    if a.check:
        line = check(a.check[0], a.check[1])
        print(line or f"(no earnings for {a.check[0]} in the calendar)")
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
