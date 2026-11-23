#!/usr/bin/env python3
"""flow_spike.py — put-premium spike detector (2026-11-18).

The WPM post-mortem found the warning we missed: on 11-05, the day we sold
the 140/135 bull puts, the tape printed $4.8M of put premium - 19.8x the
trailing daily median. Someone bought downside protection at the same strikes
we were short. We held, and stopped out -$960.

Rule: for a SHORT-PUT position, if the underlying's fresh put premium is a
multiple of its trailing daily median, flag it. This is ADVISORY (like the
earnings warning): it prints and records, never blocks, never sizes.

  python flow_spike.py WPM 2026-11-05   # calibrate + flag one entry
  python flow_spike.py --report         # archive
"""
import argparse
import collections
import csv
import datetime
import pathlib
import statistics
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
OUT = HERE / "flow_spike.csv"
COLS = ["ts", "underlying", "entry_day", "put_premium", "median", "multiple",
        "verdict"]

# 5x median = a spike worth naming. Tuned: WPM's bad day was ~20x; a normal
# earnings name runs 2-4x. 5x separates real downside protection from noise.
SPIKE_MULTIPLE = 5.0


def daily_put_premium(underlying, start, end, min_premium=5000):
    """{day: total put premium} from the LSE archive (fetch if missing)."""
    import lse_flow
    rows = lse_flow.snapshot(underlying, start, end, min_premium, log=lambda m: None)
    byd = collections.defaultdict(float)
    for r in rows:
        if (r.get("contract_type") or "").lower() == "put":
            byd[(r.get("print_ts") or "")[:10]] += float(r.get("premium") or 0)
    return byd


def flag_entry(underlying, entry_day, log=print):
    """Advisory verdict for a short-put entry on `entry_day`."""
    d = datetime.date.fromisoformat(entry_day)
    start = (d - datetime.timedelta(days=10)).isoformat()
    end = (d + datetime.timedelta(days=1)).isoformat()
    try:
        byd = daily_put_premium(underlying, start, end)
    except Exception as e:
        log(f"flow_spike: fetch failed {underlying}: {type(e).__name__}")
        return "", None

    if entry_day not in byd:
        return (f"no put-print data for {underlying} on {entry_day} "
                f"(pre-market entry?)"), None

    today_val = byd[entry_day]
    prior = sorted(v for k, v in byd.items() if k < entry_day)
    if not prior:
        return f"{underlying} {entry_day}: first-day put premium ${today_val:,.0f} (no baseline)", None

    med = statistics.median(prior)
    mult = today_val / med if med else 0.0

    verdict = ""
    if mult >= SPIKE_MULTIPLE:
        verdict = (f"⚠️ PUT-PREMIUM SPIKE {underlying} {entry_day}: "
                   f"${today_val:,.0f} vs ${med:,.0f} median = {mult:.1f}x — "
                   f"downside protection is being bought hard. A short-put "
                   f"entry here is fighting the tape.")
    else:
        verdict = (f"flow normal {underlying} {entry_day}: put premium "
                   f"{mult:.1f}x median (${today_val:,.0f} vs ${med:,.0f})")

    row = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "underlying": underlying,
           "entry_day": entry_day, "put_premium": today_val, "median": med,
           "multiple": round(mult, 2), "verdict": verdict}
    new = not OUT.exists()
    with OUT.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        if new:
            w.writeheader()
        w.writerow(row)
    log(verdict)
    return verdict, row


def report():
    if not OUT.exists():
        print("no flow_spike.csv — run: python flow_spike.py <TICKER> <DATE>")
        return 0
    rows = list(csv.DictReader(OUT.open(encoding="utf-8")))
    print(f"=== FLOW SPIKE ARCHIVE ({len(rows)} checks) ===")
    for r in rows:
        print(f"  {r['entry_day']} {r['underlying']:<7} {r['multiple']}x  "
              f"{'⚠️ SPIKE' if float(r['multiple'] or 0) >= SPIKE_MULTIPLE else 'normal'}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("underlying", nargs="?")
    ap.add_argument("entry_day", nargs="?")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        return report()
    if not (a.underlying and a.entry_day):
        print(__doc__)
        return 0
    flag_entry(a.underlying, a.entry_day)
    return 0


if __name__ == "__main__":
    sys.exit(main())
