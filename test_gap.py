"""test_gap.py — listener recovery must find OUT-OF-ORDER holes.

On 10/30 ids 3645/3646 never arrived while 3647 did; recovery keyed only on
"newer than last seen" can never fetch those again. gap_candidates scans back.
"""
import james_pipe as jp

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


seen = {"3644": 1, "3647": 1}
ids = [3647, 3646, 3645, 3644, 3643]

check("the 3645/3646 hole is found", jp.gap_candidates(seen, ids, 3647) == [3643, 3645, 3646],
      str(jp.gap_candidates(seen, ids, 3647)))
check("ids above the last seen are found too",
      jp.gap_candidates(seen, [3649, 3648] + ids, 3647) == [3643, 3645, 3646, 3648, 3649])
check("already-seen ids are skipped", jp.gap_candidates(seen, [3647, 3644], 3647) == [])
check("nothing below the backscan window", jp.gap_candidates(set(), [3000, 3647], 3647, 60) == [3647])
check("result is ordered oldest-first", jp.gap_candidates(set(), [5, 3, 4], 5) == [3, 4, 5])

print(f"\n{len(FAILS)} FAIL(S)" if FAILS else "\nALL PASS")
raise SystemExit(1 if FAILS else 0)
