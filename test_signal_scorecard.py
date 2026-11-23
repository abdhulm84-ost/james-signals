"""test_signal_scorecard.py — D143: the HIS-axis signal scorecard.

User format (2026-11-21): record every signal the moment it arrives with a
BLANK exit, fill the exit later from HIS own message (price / target-done /
'% up' claim), score WIN/LOSS on HIS numbers. No prices -> UNSCOREABLE row,
never dropped. Never guess: a bare exit/claim with 2+ open signals is refused.

Guards:
  * _save rewrites the header (the old truncation bug dropped it on save #2)
  * record-on-arrival -> PENDING, exit blank
  * exit price closes with HIS math (long +646% on his 1.73 -> 12.91 example;
    credit short (e-x)/e; x100 convention $235 -> 2.35)
  * '% up' claim closes; '2nd target done' closes at the nth target
  * trims NEVER close (partial management)
  * strike + % with no prices -> UNSCOREABLE (his SPY 747C example)
  * exit with nothing open -> no phantom row; ambiguity refused, named ok
  * entry context ('Sold ... n collected $253 for today') stays an ENTRY
  * idempotent by msg_id; report renders his exact header
  * pipe source: the hook runs BEFORE the INVALID return and the PARSE_FAIL
    drop also records (exits/claims carry no strikes -> validate_card fails)

Run: python test_signal_scorecard.py   (exit 0 = green)
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import signal_scorecard as sc          # noqa: E402

FAILS = []
CSV = pathlib.Path(__file__).parent / "signal_scorecard.guardtest.csv"


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


def fresh():
    if CSV.exists():
        CSV.unlink()
    sc.CSV = CSV


fresh()
try:
    # ---- _save keeps the header across rewrites ---------------------------
    sc.add("AAA 100C", "AAA", 1.0, "long", "2026-12-18", msg_id="t1")
    sc.add("BBB 200C", "BBB", 2.0, "long", "2026-12-18", msg_id="t2")
    rows = sc._load()
    check("_save keeps header on every rewrite (keys are COLS)",
          rows and list(rows[0].keys()) == sc.COLS,
          str(list(rows[0].keys()) if rows else None))
    fresh()

    # ---- record on arrival: entry now, exit BLANK -------------------------
    r = sc.track({"instrument": "AMD", "option": {"underlying": "AMD",
                  "strike": 555.0, "right": "call"}, "direction": "long",
                  "entry": 1.73, "expiry": "2026-12-18",
                  "targets": [3.5, 5.0], "action": "entry"},
                 "buy AMD 555C at 1.73 for 12/18", "4601")
    check("arrival -> PENDING with blank exit (his rule)",
          r and r["result"] == "PENDING" and r["exit"] == ""
          and r["entry"] == "1.73", str(r))

    # ---- his exit price closes it (his exact AMD example) -----------------
    r2 = sc.track({"instrument": "AMD"}, "sold AMD 555C at 12.91", "4602")
    check("exit price closes -> WIN +646% (his 1.73 -> 12.91)",
          r2 and r2["result"] == "WIN" and r2["return_pct"] == "+646"
          and r2["exit"] == "12.91", str(r2))

    # ---- credit short math ------------------------------------------------
    sc.track({}, "Sell RUT 2665P n Buy 2655P n collect $440 for today", "4603")
    r4 = sc.track({}, "bought back at $330 and took small gains", "4604")
    check("credit short exit (e-x)/e: 4.40 -> 3.30 = +25%",
          r4 and r4["direction"] == "short" and r4["exit"] == "3.3"
          and r4["return_pct"] == "+25" and r4["result"] == "WIN",
          str(r4))
    fresh()

    # ---- x100 convention --------------------------------------------------
    sc.track({}, "buy WMT 128C at 1.7 for 11/27", "4611")
    r6 = sc.track({}, "bought back at $235 and took small gains", "4612")
    check("x100 convention: 'at $235' -> 2.35, +38%",
          r6 and r6["exit"] == "2.35" and r6["return_pct"] == "+38",
          str(r6))
    fresh()

    # ---- '% up' claim -----------------------------------------------------
    sc.track({"instrument": "MRNA", "direction": "long", "entry": 3.48,
              "expiry": "2026-11-20", "action": "entry"},
             "buy MRNA 192.5P at 3.48 for 11/20", "4621")
    r8 = sc.track({"instrument": "MRNA"}, "80% up", "4622")
    check("bare '80% up' claim closes the pending (+80 WIN)",
          r8 and r8["return_pct"] == "+80" and r8["result"] == "WIN",
          str(r8))
    fresh()

    # ---- target-done closes at the nth stored target ----------------------
    sc.track({"instrument": "FUTU", "direction": "long", "entry": 2.3,
              "expiry": "2026-11-20", "targets": [2.8, 3.5, 4.6],
              "action": "entry"}, "buy FUTU 88C at 2.3", "4631")
    r10 = sc.track({"instrument": "FUTU"}, "2nd target done", "4632")
    check("'2nd target done' closes at target 2 (3.5, +52%)",
          r10 and r10["exit"] == "3.5" and r10["return_pct"] == "+52"
          and r10["note"] == "T2 hit", str(r10))
    fresh()

    # ---- trims never close ------------------------------------------------
    sc.track({"instrument": "FISV", "direction": "short", "entry": 1.0,
              "expiry": "2026-11-20", "action": "entry"},
             "sell FISV at 1.0", "4641")
    r12 = sc.track({"instrument": "FISV"}, "trim 80% on FISV", "4642")
    check("trim is partial management -> row untouched",
          r12 is None and sc._load()[0]["result"] == "PENDING", str(r12))
    fresh()

    # ---- his no-price example -> UNSCOREABLE row, never dropped -----------
    r13 = sc.track({}, "SPY 747C 20% up for 12/18", "4651")
    check("strike + % with no prices -> UNSCOREABLE (his SPY 747C row)",
          r13 and r13["signal"] == "SPY 747C" and r13["underlying"] == "SPY"
          and r13["result"] == "UNSCOREABLE"
          and r13["note"] == "no prices in the signal", str(r13))
    fresh()

    # ---- exit with nothing open -> no phantom row -------------------------
    r14 = sc.track({}, "sold NVDA 180C at 4.20", "4661")
    check("exit with nothing open creates no row",
          r14 is None and not sc._load(), str(r14))

    # ---- ambiguity: bare exit/claim with 2+ open = refuse ------------------
    sc.track({}, "buy WMT 128C at 1.7 for 11/27", "4671")
    sc.track({}, "buy QQQ 648C at 1.5 for 11/25", "4672")
    r15 = sc.track({}, "bought back at $235 and took small gains", "4673")
    r16 = sc.track({}, "80% up", "4674")
    both = [x["result"] for x in sc._load()]
    check("bare exit with 2 open refused (never guess)",
          r15 is None and both == ["PENDING", "PENDING"], str((r15, both)))
    check("bare claim with 2 open refused (never guess)",
          r16 is None, str(r16))
    r17 = sc.track({}, "sold QQQ 648C at 3.00", "4675")
    check("NAMED exit picks the right row (+100%)",
          r17 and r17["signal"] == "QQQ 648C"
          and r17["return_pct"] == "+100", str(r17))
    fresh()

    # ---- entry context stays an ENTRY (past tense is not an exit) ---------
    r18 = sc.track({}, "Sold QQQ 648P n collected $253 for today", "4681")
    check("'Sold ... n collected $253 for today' is an ENTRY (short)",
          r18 and r18["result"] == "PENDING" and r18["direction"] == "short"
          and r18["entry"] == "2.53", str(r18))
    fresh()

    # ---- sold WITHOUT price: note + stays pending -------------------------
    sc.track({}, "buy QQQ 648C at 1.50 for 11/25", "4691")
    r19 = sc.track({}, "sold my QQQ 648C", "4692")
    check("exit called with no price -> stays PENDING with note",
          r19 and r19["result"] == "PENDING"
          and "no price posted" in r19["note"], str(r19))
    fresh()

    # ---- idempotent by msg_id ---------------------------------------------
    sc.track({}, "buy AMD 555C at 1.73 for 12/18", "4701")
    dup = sc.track({}, "anything else", "4701")
    check("idempotent by msg_id (reprocessing safe)",
          dup is None and len(sc._load()) == 1, str(dup))
    fresh()

    # ---- year roll on 'for M/D' (D40 clock family — expectations are
    # computed from the runtime clock, never hardcoded) ---------------------
    import datetime as _dt
    _today = _dt.date.today()

    def _expect(mo, dy):
        d = _dt.date(_today.year, mo, dy)
        yr = _today.year if d >= _today - _dt.timedelta(days=3) else _today.year + 1
        return f"{yr:04d}-{mo:02d}-{dy:02d}"

    e = sc._exp_from_text("buy XYZ 100C at 1.0 for 12/18")
    check("'for 12/18' resolves against the runtime clock",
          e == _expect(12, 18), f"{e} vs {_expect(12, 18)}")
    e2 = sc._exp_from_text("buy XYZ 100C at 1.0 for 1/5")
    check("'for 1/5' rolls across the year boundary when past",
          e2 == _expect(1, 5), f"{e2} vs {_expect(1, 5)}")
    fresh()

    # ---- report renders his exact header ----------------------------------
    sc.track({}, "buy AMD 555C at 1.73 for 12/18", "4711")
    sc.track({}, "sold AMD 555C at 12.91", "4712")
    sc.track({}, "SPY 747C 20% up for 12/18", "4713")
    out = sc.report()
    check("report header is his format",
          "| # | Signal | Entry → exit (his) | Return | Result |" in out,
          out.splitlines()[2] if out else "")
    check("report shows the scored row with %",
          "| 1 | AMD 555C | 1.73 → 12.91 | +646% | ✅ WIN |" in out, out)
    check("report shows the unscoreable row",
          "SPY 747C" in out and "🚫 UNSCOREABLE" in out, out)
    check("footer counts", "scored 1 · pending 0 · unscoreable 1" in out, out)
    fresh()

    # ---- D145: the hook must be OFF under the test suite ------------------
    import sys as _sys
    _argv0 = _sys.argv[0]
    try:
        _sys.argv[0] = "test_route_4033.py"
        check("D145: enabled() False under test_* argv (no live CSV writes)",
              sc.enabled() is False, str(sc.enabled()))
        _sys.argv[0] = r"C:\Trading\james-signals\james_pipe.py"
        check("D145: enabled() True in the live pipe", sc.enabled() is True,
              str(sc.enabled()))
        _sys.argv[0] = "pytest"
        check("D145: enabled() False under pytest", sc.enabled() is False,
              str(sc.enabled()))
    finally:
        _sys.argv[0] = _argv0

    # ---- pipe source: hook placement + PARSE_FAIL coverage ----------------
    src = (pathlib.Path(__file__).parent / "james_pipe.py").read_text(
        encoding="utf-8")
    i_parse = src.index("def _parse_trade(")
    i_next = src.index("\ndef ", i_parse + 10)
    body = src[i_parse:i_next]
    i_hook = body.index("_sc.track(norm")
    i_inv = body.index('{"event": "INVALID"')
    check("D143 hook runs BEFORE the INVALID return (exits/claims carry "
          "no strikes)", i_hook < i_inv, f"hook@{i_hook} invalid@{i_inv}")
    i_fail = body.index("_sc.track({}")
    i_pf = body.index('{"event": "PARSE_FAIL"')
    check("PARSE_FAIL drop also records (brain outage keeps the scorecard "
          "complete)", i_fail < i_pf, f"track@{i_fail} fail@{i_pf}")
    check("D145: both hook sites gate on enabled()",
          body.count("_sc.enabled()") == 2,
          str(body.count("_sc.enabled()")))
finally:
    if CSV.exists():
        CSV.unlink()

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
