"""test_stale_replay.py — a re-delivered message must not open a position (D56).

The 8-day delivery outage re-stamped last week's signals with Sunday's
timestamps: "for today" anchored to the WRONG session and the age gate saw a
fresh message. The 5 resulting orders were cancelled at the broker before the
open. The fix: recovery-batch / aged-retry provenance parks the ENTRY (its
counterfactual settles in park_outcomes) while exits still process.
Run: python test_stale_replay.py   (exit 0 = green)
"""
import datetime

import james_pipe as jp

FAILS = []
PLACED = []
PARKS = []
EXITS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


jp.save_state = lambda st: None
jp.push = lambda t, s: None
jp.ledger_row = lambda r: None
jp.park_signal = lambda c, m, t, w: PARKS.append(w)
_HERMES_CALLS = []
_orig_hermes = jp.hermes_parse


def _counting_hermes(text, photo=None):
    _HERMES_CALLS.append(text)
    return {"instrument": "SPX", "direction": "short", "action": "entry",
            "entry": None, "stop": None, "targets": [], "confidence": 60,
            "horizon": "", "rationale": "", "trim": None, "spread": None}


jp.hermes_parse = _counting_hermes
jp._under_quote = lambda u: 7700.0
jp._listed = lambda u, e, r, k: (True, True, None)
jp.spread_credit_mid = lambda u, e, l: None


class _FE:
    def place(self, rec):
        PLACED.append(rec)

        class R:
            ok = True
            order_id = "T"
            venue = "tradier"
            reason = "s"
            fill_price = None
        return R()

    def order_fill(self, o):
        return "filled", 1.0

    def cancel(self, o):
        return True, "x"

    def close(self, t, limit_price=None, qty=None):
        return True, "x"

    def quote_stock(self, s):
        return 50.0

    def place_stock(self, s, side, q):
        return self.place(None)


jp._TRADIER_INST = _FE()
jp.handle_exit_update = lambda act, norm, mid, st, text: EXITS.append(mid)
jp.us_today = lambda: datetime.date(2026, 11, 17)
jp.us_session_for_today = lambda: datetime.date(2026, 11, 17)

TXT = "Sell SPX 7520C n Buy 7530C n collect $510 for today"


def run(txt, mid, stale):
    PLACED.clear()
    PARKS.clear()
    EXITS.clear()
    jp._parse_trade(txt, mid, None, {"seen_msg": {}, "open": {}, "closed": {},
                                     "alerts_today": 0}, 0, stale=stale)


# 1. the exact incident shape: a re-delivered entry parks, never orders
_HERMES_CALLS.clear()
run(TXT, "4331", True)
check("stale entry is parked, not traded", not PLACED and PARKS, f"{len(PLACED)} {PARKS[:1]}")
check("park reason names the stale re-delivery",
      PARKS and "D56" in PARKS[0], str(PARKS[:1]))
check("D58: stale message never touches the brain (no hermes call)",
      not _HERMES_CALLS, str(_HERMES_CALLS))

# 2. a fresh identical entry still trades (regression) and DOES use the brain
_HERMES_CALLS.clear()
run(TXT, "4332", False)
check("fresh entry still trades", PLACED and not PARKS, f"{len(PLACED)} {PARKS[:1]}")
check("fresh entry still consults the brain", bool(_HERMES_CALLS), str(_HERMES_CALLS))

# 3. exits on stale messages still process (management is safe)
run("Pl book gains on all SPX Bull Puts", "4333", True)
check("stale exit still processes (management)", EXITS and not PLACED, f"{EXITS} {len(PLACED)}")

# 4. lotto/single stale entries park too (the O1794790761 shape)
run("Buy 7530C for $21 for today - Lotto", "4343", True)
check("stale lotto entry parks", not PLACED and PARKS, f"{len(PLACED)} {PARKS[:1]}")

# ---- D62: live messages jump the backlog; stale drain in bulk ----------
NOW = 1_000_000.0
q = {
    "4387": {"text": "live entry", "tries": 2, "at": NOW - 4000, "stale": False},
    "4155": {"text": "old", "tries": 2, "at": NOW - 5000},            # legacy = stale
    "4156": {"text": "old", "tries": 1, "at": NOW - 900, "stale": True},
}
batch = jp._retry_batch(q, now=NOW)
check("D62: live message jumps the stale backlog", batch and batch[0][0] == "4387",
      str([b[0] for b in batch]))
q2 = {str(i): {"text": "old", "tries": 2, "at": NOW - 4000, "stale": True}
      for i in range(50)}
check("D62: stale items drain in bulk (20/cycle)",
      len(jp._retry_batch(q2, now=NOW)) == 20, str(len(jp._retry_batch(q2, now=NOW))))
q3 = {"a": {"text": "x", "tries": 1, "at": NOW - 4000, "stale": False},
      "b": {"text": "x", "tries": 1, "at": NOW - 3000, "stale": False}}
check("D62: live items take the OLDEST attempt first (FIFO)",
      [m for m, _ in jp._retry_batch(q3, now=NOW)] == ["a", "b"],
      str([m for m, _ in jp._retry_batch(q3, now=NOW)]))
q4 = {str(i): {"text": "x", "tries": 1, "at": NOW - 4000 - i, "stale": False}
      for i in range(8)}
check("D62: live bursts clear at 5 per cycle",
      len(jp._retry_batch(q4, now=NOW)) == 5, str(len(jp._retry_batch(q4, now=NOW))))
check("D62: not-yet-due items wait their backoff",
      jp._retry_batch({"c": {"text": "x", "tries": 1, "at": NOW - 1, "stale": False}},
                      now=NOW) == [])

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
