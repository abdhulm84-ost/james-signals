"""test_direction_sold.py — D110 past-tense sell detection (2026-11-19).

The original miss: msg 4489 'Sold QQQ 657P for today and collected $265' was
booked as a LONG because `text_is_naked_short` only matched the present-tense
'sell' and never 'sold'/'collected'. James SOLD the put (short = collect
premium), QQQ went UP, and the short put won — but the book recorded it long
and computed the target on the wrong side.

Rule: 'sold/selling/wrote/shorting' + 'collected' are short-credit signals;
'bought/buying/paid' are long. Past tense must read the same as present.
Run: python test_direction_sold.py   (exit 0 = green)
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import james_pipe as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


# the 4489 miss
check("4489 'Sold ... collected $265' -> short (naked put)",
      jp.text_is_naked_short("Sold QQQ 657P for today and collected $265 for today - Funded Acct") is True)

# present tense still short
check("present 'Sell ... collect' still short",
      jp.text_is_naked_short("Sell QQQ 657P for today and collect $265") is True)

# past-tense collect without sell verb
check("'Sold QQQ 652P n collected $125' short",
      jp.text_is_naked_short("Sold QQQ 652P n collected $125 - Funded - 3C") is True)

# long mirror stays long
check("'Buy QQQ 657P' NOT short",
      jp.text_is_naked_short("Buy QQQ 657P for today") is False)
check("'Bought ... paid' NOT short",
      jp.text_is_naked_short("Bought QQQ 657P for today and paid $265") is False)

# a spread with collect is excluded (has buy + sell verbs)
check("spread 'Sell ... n Buy ... collect' NOT naked",
      jp.text_is_naked_short("Sell SPX 7600P n Buy 7590P n collect $475") is False)

# narrative 'enter IWM Puts' is a long, not a short
check("'enter IWM Puts' NOT short",
      jp.text_is_naked_short("enter IWM Puts for Monday") is False)

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
