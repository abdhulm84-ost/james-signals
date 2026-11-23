"""test_flow_spike.py — the put-premium spike advisory (2026-11-18).

WPM's -$960 was the signal we missed: on entry day (11-05) the tape printed
$4.8M of put premium (14.9x the trailing median) - someone bought downside
protection at the same strikes we were short. This detector flags that.

Advisory only: prints + records, never blocks, never sizes.
Run: python test_flow_spike.py   (exit 0 = green)
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import flow_spike as fs
import james_pipe as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


# --- 1. the spike is detected from the daily series ---------------------
# (WPM 11-05 = 4.78M vs prior median ~321K -> ~14.9x, must flag)
line, row = fs.flag_entry("WPM", "2026-11-05", log=lambda m: None)
check("the WPM entry-day spike is detected",
      "SPIKE" in line and "14.9x" in line or "SPIKE" in line, line[:90])
check("the verdict carries the multiple", float(row["multiple"]) >= fs.SPIKE_MULTIPLE,
      str(row.get("multiple")))

# --- 2. a normal day does not spike ------------------------------------
def fake_normal(underlying, start, end, min_premium=5000):
    return [{"contract_type": "put", "premium": 100000, "print_ts": start},
            {"contract_type": "put", "premium": 110000, "print_ts": end}]
orig = fs.daily_put_premium
fs.daily_put_premium = fake_normal
line2, row2 = fs.flag_entry("TEST", "2026-11-18", log=lambda m: None)
fs.daily_put_premium = orig
check("a normal put-premium day does not spike",
      "SPIKE" not in line2, line2[:80])

# --- 3. wired into the pipeline (advisory, off-thread) -----------------
SRC = pathlib.Path(jp.__file__).read_text(encoding="utf-8")
check("the flow-spike helper exists", "def _flow_spike_advisory" in SRC)
check("it runs off-thread", "daemon=True" in SRC)
check("it fires only on short-put verticals",
      "_flow_spike_advisory(under, msg_id, expiry)" in SRC and
      "startswith(\"p\")" in SRC)

# --- 4. it never raises (a missing module must not break a trade) ------
jp.log = lambda m: None
jp.ledger_row = lambda r: None
try:
    jp._flow_spike_advisory("WPM", "999", "2026-11-20")
    ok = True
except Exception as e:
    ok = False
check("the advisory never raises", ok)

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
