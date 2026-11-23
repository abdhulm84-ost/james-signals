#!/usr/bin/env python3
"""ticker_taxonomy.py — canonical ticker buckets, the way James's journal does it
(2026-11-18, user request: "he has segregated all the tickers, we have SPX
segregated, can we follow the same through our pipeline?").

Why it matters: our ledger carries 110 DISTINCT ticker strings for far fewer
real instruments. SPX arrives as SPX / SPXW / SPX500; plus junk keys leak in
(OCC contracts as "symbols", the "MK" signature). Fragmented keys mean:
  * the pattern table splits one instrument across several buckets
  * the UW flow lookup has to guess which ticker to query
  * per-instrument win rates are computed on 1/3 of the data

  python ticker_taxonomy.py --report        # the buckets + counts
  python ticker_taxonomy.py --class SPXW    # one ticker's bucket
"""
import argparse
import csv
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent

# OCC contract code, e.g. BLK261106P01185000 — a CONTRACT, never a ticker
_OCC = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")
# prose noise that the parser occasionally keeps as an "instrument"
_JUNK = {"MK", "-", "N/A", "NA", "NONE", "NULL", "UNKNOWN", "", "TXT", "IMG"}

INDEX_ROOTS = {
    "SPX": "SPX", "SPXW": "SPX", "SPX500": "SPX", "SPXW500": "SPX",
    "S&P500": "SPX", "S&P": "SPX", "ES": "SPX", "SPX50": "SPX",
    "RUT": "RUT", "RUTW": "RUT", "RUSSELL": "RUT", "RUSSELL2000": "RUT",
    "RTY": "RUT",
    "NDX": "NDX", "NDXW": "NDX", "NDX100": "NDX", "NAS100": "NDX",
    "NASDAQ": "NDX", "USTEC": "NDX", "NQ": "NDX",
    "VIX": "VIX", "DJI": "DJI", "YM": "DJI",
}

# broad-market / sector ETFs we see James trade
ETF_ROOTS = {
    "SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "TLT", "SOXL", "SOXS",
    "TQQQ", "SQQQ", "UVXY", "XLF", "XLE", "XLK", "SMH", "ARKK", "IBIT",
}

# index roots whose "ETF" is really the same exposure (for bucketing only)
INDEX_IN_PROXY = {"SPX": "SPY", "NDX": "QQQ", "RUT": "IWM", "DJI": "DIA"}


def normalise(raw):
    """(canonical, bucket). Canonical is the instrument you would actually
    quote; bucket is the journal tab: index | etf | stock | contract | junk."""
    u = str(raw or "").strip().upper()
    u = re.sub(r"\s+", " ", u)
    if not u or u in _JUNK:
        return None, "junk"
    first = u.split()[0]
    if _OCC.match(first.replace(" ", "")):
        return None, "contract"          # an OCC code is not an instrument
    key = first.replace(" ", "")
    if key in INDEX_ROOTS:
        return INDEX_ROOTS[key], "index"
    if key in ETF_ROOTS:
        return key, "etf"
    # bare uppercase word 1-6 chars: an equity ticker
    if re.fullmatch(r"[A-Z]{1,6}", key):
        return key, "stock"
    return None, "junk"


def bucket_of(raw):
    return normalise(raw)[1]


def canonical_of(raw):
    return normalise(raw)[0]


def report(ledger=None):
    ledger = ledger or (HERE / "signals.csv")
    counts = {}
    seen = {}
    if ledger.exists():
        with ledger.open(encoding="utf-8", errors="replace") as f:
            for r in csv.DictReader(f):
                raw = (r.get("instrument") or "").strip()
                if not raw:
                    continue
                canon, bucket = normalise(raw)
                key = canon or f"(dropped:{bucket})"
                counts[key] = counts.get(key, 0) + 1
                seen.setdefault(key, set()).add(raw.split()[0])
    by_bucket = {}
    for key, n in counts.items():
        b = bucket_of(key.replace("(dropped:", "").replace(")", "")) if not key.startswith("(dropped") else key[9:-1]
        by_bucket.setdefault(b, []).append((key, n))
    print(f"=== TICKER TAXONOMY ({len(counts)} canonical instruments) ===")
    for b in ("index", "etf", "stock", "contract", "junk"):
        items = sorted(by_bucket.get(b, []), key=lambda kv: -kv[1])
        if not items:
            continue
        print(f"\n-- {b.upper()} ({len(items)})")
        for key, n in items[:18]:
            srcs = sorted(seen.get(key, []))
            extra = f"  <- from {srcs}" if len(srcs) > 1 else ""
            print(f"   {key:<10} {n:>5}{extra}")
        if len(items) > 18:
            print(f"   ... and {len(items) - 18} more")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--class", dest="cls", default="")
    a = ap.parse_args()
    if a.report:
        return report()
    if a.cls:
        c, b = normalise(a.cls)
        print(f"{a.cls} -> canonical={c} bucket={b}")
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
