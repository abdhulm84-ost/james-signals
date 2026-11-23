"""
test_james_core.py — regression + integration tests for the v2 core.

Covers the real messages from the 27-28 Oct review so the model is pinned to
reality, not to my assumptions. Run:  python test_james_core.py
"""
import sys
from james_core import (
    Record, MsgClass, Action, RiskTier, RiskPolicy, ExitEngine, ExitType,
    ScopeResolver, text_strikes, has_command, Leg,
)

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


# ---------------------------------------------------------------- text helpers
check("strikes: 3-digit (106P) + 4-digit (7735C)",
      text_strikes("UPS Sell 106P n Buy 102P; SPX 7735C") == [102.0, 106.0, 7735.0])
check("command: 'Sell ... collect' yes", has_command("Sell SPX 7735C n Buy 7745C n collect 550"))
check("command: 'buy AMKR' yes", has_command("buy AMKR 38C at 1.8"))
check("command: 'we are holding ... will drop' NO",
      not has_command("We are holding Bear calls from yesterday. we will see a big drop"))

# ---------------------------------------------------------------- record metrics
r = Record(msg_id=1, ts="", cls=MsgClass.VERTICAL, action=Action.ENTRY, raw="",
           credit=4.15, width=5.0, risk_stated=85, reward_stated=415)
check("quality metric credit/width = 0.83", abs(r.credit_pct_width - 0.83) < 0.001)
check("reward_risk RUT 2790/2795 = 4.88:1", abs(r.reward_risk - 4.88) < 0.02)
check("L38 derive width from risk/reward = 5.0",
      Record(msg_id=2, ts="", cls=MsgClass.FLY, action=Action.ENTRY, raw="",
             credit=4.62, risk_stated=38, reward_stated=462).derive_width_from_risk_reward() == 5.0)

# ---------------------------------------------------------------- risk policy
pol = RiskPolicy(phase=1)

# hard gate = risk TYPE
lotto = Record(msg_id=3, ts="", cls=MsgClass.LOTTO, action=Action.ENTRY, raw="",
               credit=1.62, width=None, risk_tier=RiskTier.LOTTO)
ok, why = pol.allow(lotto)
check("Phase1: lotto blocked", not ok, why)

naked = Record(msg_id=4, ts="", cls=MsgClass.NAKED, action=Action.ENTRY, raw="",
               credit=1.20, direction="short")
ok, why = pol.allow(naked)
check("Phase1: naked SHORT blocked", not ok, why)

# 4017: a narrative long on NQ/ES is classed NAKED with no legs — a cash asset
# long loses only to zero, so it must pass (it was being risk-blocked)
naked_long_asset = Record(msg_id=41, ts="", cls=MsgClass.NAKED, action=Action.ENTRY,
                          raw="", direction="long")
ok, why = RiskPolicy(phase=2).allow(naked_long_asset)
check("naked-classed LONG asset (no legs) is defined", ok, why)

naked_short_asset = Record(msg_id=42, ts="", cls=MsgClass.NAKED, action=Action.ENTRY,
                           raw="", direction="short")
ok, why = RiskPolicy(phase=2).allow(naked_short_asset)
check("naked-classed SHORT asset (no legs) stays blocked", not ok, why)

csp = Record(msg_id=43, ts="", cls=MsgClass.NAKED, action=Action.ENTRY, raw="",
             direction="short", credit=2.0,
             legs=[Leg(strike=7000, right="put", action="sell")])
ok, why = RiskPolicy(phase=2).allow(csp)
check("naked SHORT PUT with a leg is cash-secured, allowed", ok, why)

vert = Record(msg_id=5, ts="", cls=MsgClass.VERTICAL, action=Action.ENTRY, raw="",
              credit=1.96, width=5.0)
ok, why = pol.allow(vert)
check("Phase1: defined-risk vertical ALLOWED (even low quality)", ok, why)

# low quality is NOT rejected (the over-filter guard)
lowq = Record(msg_id=6, ts="", cls=MsgClass.VERTICAL, action=Action.ENTRY, raw="",
              credit=1.96, width=5.0)   # 39% credit/width -> below threshold
ok, why = pol.allow(lowq)
check("balance guard: low-quality defined-risk still allowed", ok, why)
check("balance guard: size is reduced not zero",
      pol.size(lowq, 100000) < pol.size(Record(msg_id=7, ts="", cls=MsgClass.VERTICAL,
                                              action=Action.ENTRY, raw="", credit=4.15, width=5.0), 100000))

# context is not a trade
for cls in (MsgClass.MAP, MsgClass.PREDICTION, MsgClass.NARRATIVE, MsgClass.DECAY, MsgClass.GOAL):
    ok, why = pol.allow(Record(msg_id=8, ts="", cls=cls, action=Action.NONE, raw=""))
    check(f"context {cls.value} not traded", not ok)

# phase 2 allows lotto
pol2 = RiskPolicy(phase=2)
ok, why = pol2.allow(lotto)
check("Phase2: lotto allowed", ok, why)

# sizing respects buying power
check("sizing capped by buying power", pol.size(vert, 100.0) <= 25.0)
check("lotto sized smallest",
      pol.size(lotto, 100000) < pol.size(vert, 100000))

# ---------------------------------------------------------------- exit engine
eng = ExitEngine()

def exit_rec(**kw):
    base = dict(msg_id=9, ts="", cls=MsgClass.EXIT, action=Action.EXIT, raw="")
    base.update(kw)
    return Record(**base)

# L27 explicit close
ex, tag = eng.resolve(exit_rec(underlying="SPX500", legs=[
    Leg(7730, "put", "sell_to_close"), Leg(7735, "put", "buy_to_close")]), [])
check("exit: explicit close (L27) verbatim", tag == "explicit" and ex.kind == ExitType.EXPLICIT_CLOSE)

# L15 scoped trim
ex, tag = eng.resolve(exit_rec(scope={"time_window": "today", "structure_type": "bear_call"},
                               pct_claimed=80), [])
check("exit: scoped trim 80% (L15)", tag == "scope" and abs(ex.close_fraction - 0.80) < 0.001)

# L23 resting limit
ex, tag = eng.resolve(exit_rec(exit_by={"type": "limit", "price": 4.4}), [])
check("exit: resting limit (L23)", tag == "resting" and ex.limit_price == 4.4)

# L18/L43 time + session
ex, tag = eng.resolve(exit_rec(exit_by={"type": "session", "venue": "rh", "at": "19:15"}), [])
check("exit: session-open (L43)", tag == "time" and ex.venue_session == "rh")

# L35 conditional when-green
ex, tag = eng.resolve(exit_rec(exit_by={"type": "in_profit"}), [])
check("exit: conditional when-green (L35)", tag == "in_profit")

# L36 discretionary stop
ex, tag = eng.resolve(exit_rec(exit_by={"type": "stop_loss"}), [])
check("exit: discretionary stop (L36)", tag == "stop")

# plain
ex, tag = eng.resolve(exit_rec(underlying="GLW"), [])
check("exit: plain named exit", tag == "named")

# ---------------------------------------------------------------- scope resolver
sr = ScopeResolver()

class Pos:
    def __init__(self, **kw):
        self.__dict__.update(kw)

positions = [
    Pos(id="a", structure_type="bear_call", direction="short", sector="chip", expiry="2026-11-06",
        underlying_class="equity_option", opened_today=True, targets=[]),
    Pos(id="b", structure_type="bear_call", direction="short", sector="index", expiry="2026-11-06",
        underlying_class="index_option", opened_today=True, targets=[3.9, 5.1, 6.6]),
    Pos(id="c", structure_type="bull_put", direction="short", sector="chip", expiry="2026-11-06",
        underlying_class="equity_option", opened_today=False, targets=[]),
]

# L32 sector scope
m, why = sr.match({"sector": "chip", "structure_type": "bear_call"}, positions)
check("scope: sector+structure picks the right set", [p.id for p in m] == ["a"], why)

# L37 expiry scope
m, why = sr.match({"structure_type": "bear_call", "expiry": "2026-11-06"}, positions)
check("scope: expiry resolves 2", len(m) == 2, why)

# L21 ladder -> single target-bearing position
ladder_rec = Record(msg_id=10, ts="", cls=MsgClass.EXIT, action=Action.TRIM,
                    raw="", targets=[3.9, 5.1, 6.6], underlying="GLW")
p, why = sr.match_ladder(ladder_rec, positions)
check("ladder: single target-bearing match (L21)", p is not None and p.id == "b", why)

# L21 ambiguous -> alert, do not trade
positions2 = [Pos(id="x", targets=[1,2,3], structure_type="v"), Pos(id="y", targets=[4,5,6], structure_type="v")]
p, why = sr.match_ladder(ladder_rec, positions2)
check("ladder: ambiguous -> no trade (L21)", p is None and "AMBIGUOUS" in why, why)


print()
print(f"{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILURES: ' + str(FAILS)}")
sys.exit(1 if FAILS else 0)
