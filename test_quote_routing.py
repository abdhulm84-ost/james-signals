"""test_quote_routing.py — index roots never reach Tiger (D100).

The watch log showed `quote failed SPXW: ApiException code=4 quote_delay rate
limit` and `code=1010 biz param error`. Root cause: for an index root, when
BlackBull/Tradier returned nothing the chain fell through to Tiger's
get_stock_briefs — which rejects non-stock symbols, spamming the rate-limiter.

Index quotes belong to BlackBull/Tradier. Tiger is for EQUITIES only.
Run: python test_quote_routing.py   (exit 0 = green)
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import james_pipe as jp
import ticker_taxonomy as tx

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


# --- structural ----------------------------------------------------------
SRC = pathlib.Path(jp.__file__).read_text(encoding="utf-8")
check("get_quote guards on the ticker bucket",
      "bucket_of(sym)" in SRC and "return None" in SRC)

# --- behavioural: index roots are blocked BEFORE any Tiger call -----------
for root in ("SPX", "SPXW", "NDX", "NDX100", "RUT", "VIX"):
    check(f"{root} is an index -> get_quote returns None",
          jp.get_quote(root) is None, str(jp.get_quote(root)))

# junk/contract never reach Tiger either
check("an OCC contract returns None", jp.get_quote("SPXW261105C07700000") is None)
check("prose junk returns None", jp.get_quote("MK") is None)

# --- equities are NOT blocked --------------------------------------------
check("an ETF (QQQ) is allowed through to Tiger",
      tx.bucket_of("QQQ") == "etf")
check("a stock (WPM) is allowed through to Tiger",
      tx.bucket_of("WPM") == "stock")

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
