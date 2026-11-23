"""test_credit_verbs.py — gerund and misspelt collect forms must read the credit.

3426 "collecting around $120 premium" and 3501 "collecting $135" both parsed
None before this: the regex only knew collect/collected. Both are naked QQQ
call shorts, so the missing credit also broke any quality ratio.
Run: python test_credit_verbs.py   (exit 0 = green)
"""
import james_pipe as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


check("gerund + 'around'", jp.text_credit(
    "Selling QQQ 720C for today n collecting around $120 premium - For Funded") == 1.2)
check("gerund plain", jp.text_credit(
    "QQQ Selling 724C for today n collecting $135 - A+ for Funded") == 1.35)
check("plain collect still works", jp.text_credit(
    "Sell SPX 7640P n Buy 7630P n collect $470 for 11/2") == 4.7)
check("collected still works", jp.text_credit("collected $380") == 3.8)
check("range lower bound", jp.text_credit(
    "Sell SPX 7415C n Buy 7425C n collect $455 to $460") == 4.55)
check("2KC suffix stripped", jp.text_credit(
    "Sell SPX 7530C n Buy 7545C n collect $500 - 2KC - 2C") == 5.0)
check("no number -> None", jp.text_credit(
    "SPX Sell 7750C n Buy 7760C n coleect premiums for today - Doubel size") is None)
check("narrative no false positive", jp.text_credit(
    "SPX looking choppy between 7395 and 7445") is None)

print(("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
raise SystemExit(0 if not FAILS else 1)
