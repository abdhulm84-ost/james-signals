"""test_scanner.py — a scanned TABLE on the image is the content (msg 3654).

The text of a scanner message names no tickers; the 30 scored rows live on the
screenshot. They must be captured, and the rule must be preserved verbatim.
Run: python test_scanner.py   (exit 0 = green)
"""
import james_pipe as jp

FAILS = []
ROWS = []
PUSHED = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


jp.save_state = lambda st: None
jp.ledger_row = lambda r: ROWS.append(r)
jp.push = lambda t, s: PUSHED.append(t)

TXT = ("Our Swing scanner gave nearly perfect numbers for the earnings. Score above 60 "
       "good for Long and score below -60 good for Short.")
norm = {"instrument": "SPX500", "scanner_rows": [
    {"ticker": "AN", "score": 100, "verdict": "BUY"},
    {"ticker": "GD", "score": 100, "verdict": "BUY"},
    {"ticker": "AMZN", "score": -100, "verdict": "SELL"}]}

jp.scanner_record(norm, "3654", TXT, {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0})

tbl = [r for r in ROWS if r["event"] == "SCANNER_TABLE"]
check("every scanned row is stored", tbl and tbl[0]["targets"].count("|") == 2, str(tbl))
check("scores and verdicts kept", tbl and "AMZN:-100:SELL" in tbl[0]["targets"])
check("rule preserved verbatim", any(">= 60" in (r.get("targets") or "") for r in ROWS))
check("longs surfaced on the card", PUSHED and "AN:100" in PUSHED[0], str(PUSHED[:1]))
check("shorts surfaced on the card", PUSHED and "AMZN:-100" in PUSHED[0])
check("never auto-traded", "not auto-traded" in (PUSHED[0] if PUSHED else ""))

print(f"\n{len(FAILS)} FAIL(S)" if FAILS else "\nALL PASS")
raise SystemExit(1 if FAILS else 0)
