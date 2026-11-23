"""test_pred.py — prediction capture + automatic outcome settlement.

Predictions used to be a log line only: nothing was written to the scoring file
and only level claims could ever settle. Both are covered here.
Run: python test_pred.py   (exit 0 = green)
"""
import csv
import os

import james_pipe as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


jp.log = lambda m: None
jp.push = lambda t, s: None
TMP = "tmp_test_pred.csv"
jp.PRED_FILE = jp.Path(TMP)
PRICES = {"RDDT": 145.0, "SPX": 7700.0}
jp._under_quote = lambda s: PRICES.get(s)


def fresh():
    if os.path.exists(TMP):
        os.remove(TMP)


def rows():
    with open(TMP, encoding="utf-8") as f:
        return list(csv.reader(f))[1:]


# --- capture ------------------------------------------------------------
fresh()
jp.record_prediction({"instrument": "SPX500"}, "9001",
                     "25% drop on RDDT, our 120/125 bear calls will make 100% at open")
r = rows()[0]
check("direction claim captured with its ticker reference",
      r[3] == "direction" and r[4] == "RDDT@145.00", str(r[3:5]))
check("captured rows start OPEN", r[7] == "open")
jp.record_prediction({"instrument": "SPX500"}, "9002", "7660 to 7670 possible today")
check("level claim captured as level", rows()[1][3] == "level")

# --- settlement ---------------------------------------------------------
fresh()
with open(TMP, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(jp.PRED_HEAD)
    w.writerow(["2026-11-05", "m1", "25% drop on RDDT", "direction",
                "RDDT@145.00", "will", "", "open", "", "", "auto"])
    w.writerow([jp.today(), "m2", "same session claim", "direction",
                "RDDT@145.00", "should", "", "open", "", "", "auto"])
    w.writerow(["2026-11-05", "m3", "RDDT will bounce", "direction",
                "RDDT@145.00", "will", "", "open", "", "", "auto"])
PRICES["RDDT"] = 140.0
jp.settle_predictions({})
out = rows()
check("falling call settles CORRECT", out[0][7] == "correct", out[0][7])
check("same-session claim stays open", out[1][7] == "open", out[1][7])
check("rising call settles WRONG", out[2][7] == "wrong", out[2][7])
check("outcome text records the move", "-3.45%" in out[0][6], out[0][6])

# --- strike frame is scored beside direction ----------------------------
PRICES["RDDT"] = 145.0
pct, side = jp.strike_frame("our 120/125 bear calls", 145.0)
check("bear-call frame is NEGATIVE when the strike is already ITM",
      pct < 0 and side == "C", f"{pct},{side}")
pct2, side2 = jp.strike_frame("our bull puts 120/125", 145.0)
check("bull-put frame is positive when OTM", pct2 > 0 and side2 == "P", f"{pct2},{side2}")

fresh()
jp.record_prediction({"instrument": "RDDT"}, "9100",
                     "500pt drop on RDDT, our 120/125 bear calls will make 100%")
check("strike room stored on the row", rows()[0][11] == "-17.24", str(rows()[0][11]))

# held after a drop; blown after a rally
for px, want in ((110.0, "True"), (160.0, "False")):
    fresh()
    with open(TMP, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(jp.PRED_HEAD)
        w.writerow(["2026-11-05", "m9", "drop on RDDT, our 120/125 bear calls",
                    "direction", "RDDT@145.00", "will", "", "open", "", "", "auto",
                    "-17.24", ""])
    PRICES["RDDT"] = px
    jp.settle_predictions({})
    check(f"strike_held settles {want} at {px}", rows()[0][12] == want, str(rows()[0][12]))

# --- 3660: a Kalshi claim is scored, "yesterday's close" is not a verb ------
k = "Expecting SPX to stay above 7635 by EOD. Expecting SPX to be below Yesterday's close."
check("Kalshi claim classifies as prediction", jp.non_entry_class(k) == "prediction",
      jp.non_entry_class(k))
check("noun 'close' does not fire an exit", not jp.exit_command_wins(k))
check("a real close still fires",
      jp.exit_command_wins("Close QCOM 177.5/182.5 Bear calls"))
check("'7630 is Strong Support' captures the level",
      (7630.0, "support") in jp.text_levels("SPX 7630 is Strong Support"))

# --- max_open ceiling ---------------------------------------------------
check("max_open raised for reconciled book", jp.CFG["max_open"] >= 20, str(jp.CFG["max_open"]))

fresh()
print(f"\n{len(FAILS)} FAIL(S)" if FAILS else "\nALL PASS")
raise SystemExit(1 if FAILS else 0)
