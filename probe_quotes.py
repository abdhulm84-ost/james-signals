#!/usr/bin/env python3
"""probe_quotes — run AT THE US OPEN (13:30 UTC = 02:30 NZ) to prove the quote
adapter against live markets. Reports each path: sandbox index contract, prod
equity contract, prod ETF proxy, and the full opt_quote chain.

Run:  python probe_quotes.py
Cost: zero credits; read-only market data.
"""
import json
import time

import james_pipe as jp


def hdr(s):
    print("\n== " + s + " ==")


def main():
    hdr("sandbox index contract (SPXW)")
    try:
        print("raw:", jp._fetch_quote("sandbox", "SPXW261107C07700000"))
    except Exception as e:
        print("ERR", type(e).__name__, e)

    hdr("prod equity contract (BMRN 60C 11/20)")
    try:
        print("raw:", jp._fetch_quote("prod", "BMRN261120C00060000"))
    except Exception as e:
        print("ERR", type(e).__name__, e)

    hdr("prod ETF proxy (SPY 770C 11/7 via scale)")
    try:
        pocc, div = jp._proxy_occ("SPXW261107C07700000")
        raw = jp._fetch_quote("prod", pocc) if pocc else None
        print("proxy occ:", pocc, "div:", div, "| raw:", raw,
              "| scaled:", (raw * div) if raw else None)
    except Exception as e:
        print("ERR", type(e).__name__, e)

    hdr("full opt_quote chain (5s each)")
    jp._Q_CACHE.clear()
    for occ in ("SPXW261107C07700000", "BMRN261120C00060000"):
        t0 = time.time()
        print(occ, "->", jp.opt_quote(occ), f"({time.time()-t0:.1f}s)")


if __name__ == "__main__":
    main()
