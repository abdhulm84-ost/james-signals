"""test_d147_stale.py — stale-entry floor gate + filled-only fast scope.

D147: msg 4524 pasted his morning numbers ("buy 7200/7220 for $13.8") at
17:24 UTC while SPX sat 7,242.95 — the package's own intrinsic floor was
20.00. Both entry orders (image: 7200/7220, text-typo: 7200/7270 from
"Sell 70C") rested under the market for ~5h and expired zero-filled
(Tradier avg_fill 0.0). The gate parks the signal the moment the spot
proves the stated entry impossible (floor > ref x 1.15) — the order never
zombies. Credit packages stay exempt by shape (a sell limit fills better
when the market rises), and the vision-repair path (3956) still trades.

D148: _exit_fast counted pending_entry rows as instruments, so a book of
{SPXW unfilled, QQQ filled} read as 2 and the bare exit was refused.
Unfilled entries are not exposure (D67's own rule) — 2+ FILLED still refuse.

Run: python test_d147_stale.py   (exit 0 = green)
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import james_pipe as jp          # noqa: E402

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


# ---- harness (offline: every seam stubbed) -------------------------------
PUSHES, PLACED = [], []
jp.save_state = lambda st: None
jp.push = lambda t, s: PUSHES.append(t)
jp.ledger_row = lambda row: None
jp._load_book = lambda: jp.Book()
jp._save_book = lambda b: None
_SPOTS = {"SPX": 7242.95, "SPXW": 7242.95, "SPX500": 7242.95, "QQQ": 645.0}
jp._under_quote = lambda u: _SPOTS.get(str(u or "").upper())
jp.spread_credit_mid = lambda *a, **k: None   # quote-blind (the dte0 case)


class _FakeTradier:
    venue = "tradier"

    def place(self, rec):
        PLACED.append(rec)
        return type("R", (), {"ok": True, "reason": "", "order_id": "T1"})()


jp._TRADIER_INST = _FakeTradier()

BUY_DEBIT = [{"action": "buy", "strike": 7200.0, "right": "call"},
             {"action": "sell", "strike": 7220.0, "right": "call"}]
SELL_CREDIT = [{"action": "sell", "strike": 7200.0, "right": "call"},
               {"action": "buy", "strike": 7220.0, "right": "call"}]


def fresh_st():
    return {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0,
            "shadow": {}}


def norm_for(legs, entry=13.8):
    return {"instrument": "SPX", "direction": "long", "entry": entry,
            "spread": {"type": "credit", "legs": legs},
            "confidence": 70, "horizon": "day", "rationale": "test"}


# ---- spread_intrinsic geometry -------------------------------------------
check("intrinsic: 7200/7220C at 7242.95 = width 20 (the 4524 floor)",
      jp.spread_intrinsic(BUY_DEBIT, 7242.95) == 20.0)
check("intrinsic: at 7210 = 10", jp.spread_intrinsic(BUY_DEBIT, 7210.0) == 10.0)
check("intrinsic: at 7195 = 0", jp.spread_intrinsic(BUY_DEBIT, 7195.0) == 0.0)
check("intrinsic: no spot -> None", jp.spread_intrinsic(BUY_DEBIT, None) is None)
check("intrinsic: mixed rights -> None",
      jp.spread_intrinsic([{"strike": 100.0, "right": "call"},
                           {"strike": 95.0, "right": "put"}], 100.0) is None)

# ---- D147: the live 4524 shape parks --------------------------------------
PUSHES.clear(); PLACED.clear()
st = fresh_st()
jp.open_multileg_tradier(norm_for(BUY_DEBIT, 13.8),
                         "Buy 7200C n Sell 7220C for $13.8 for today", "9471", st)
check("D147: stale debit parks (no order placed)",
      len(PLACED) == 0, str(len(PLACED)))
check("D147: stale push fires with the floor vs his number",
      any("STALE ENTRY" in p and "13.80" in p and "20.00" in p for p in PUSHES),
      str(PUSHES[-1:]))

# ---- D147: a fresh (reachable) entry still places -------------------------
PUSHES.clear(); PLACED.clear()
_SPOTS["SPXW"] = 7205.0; _SPOTS["SPX"] = 7205.0   # floor 5.00 < 13.8 x 1.15
st2 = fresh_st()
jp.open_multileg_tradier(norm_for(BUY_DEBIT, 13.8),
                         "Buy 7200C n Sell 7220C for $13.8 for today", "9472", st2)
check("D147: fresh debit (floor 5) places normally", len(PLACED) == 1,
      str(len(PLACED)))
check("D147: fresh entry has no STALE push",
      not any("STALE ENTRY" in p for p in PUSHES), str(PUSHES))

# ---- D147: credit package exempt by shape ---------------------------------
PUSHES.clear(); PLACED.clear()
_SPOTS["SPXW"] = 7242.95; _SPOTS["SPX"] = 7242.95
st3 = fresh_st()
jp.open_multileg_tradier(norm_for(SELL_CREDIT, 4.4),
                         "Sell 7200C n Buy 7220C n collect $4.40 for today", "9473", st3)
check("D147: credit package exempt (sell limit fills better, never zombies)",
      len(PLACED) == 1, str(len(PLACED)))

# ---- D147: no spot -> cannot judge -> places (never blocks on dead feed) --
PUSHES.clear(); PLACED.clear()
_REAL_UQ = jp._under_quote
jp._under_quote = lambda u: None
st4 = fresh_st()
jp.open_multileg_tradier(norm_for(BUY_DEBIT, 13.8),
                         "Buy 7200C n Sell 7220C for $13.8 for today", "9474", st4)
jp._under_quote = _REAL_UQ
check("D147: no spot quote -> places (an anchor never blocks on dead data)",
      len(PLACED) == 1, str(len(PLACED)))

# ---- D147: the 3956 vision-repair path still trades at the mid ------------
# (repair only exists on the collect/credit path — debit texts carry no
#  tcredit, which is exactly why the 4524 order went out unrepaired)
PUSHES.clear(); PLACED.clear()
_RCM = jp.spread_credit_mid
jp.spread_credit_mid = lambda *a, **k: 3.00     # market's own number
st5 = fresh_st()
jp.open_multileg_tradier(norm_for(SELL_CREDIT, 4.4),
                         "Sell 7200C n Buy 7220C n collect $4.40 for today", "9475", st5)
jp.spread_credit_mid = _RCM
check("D147: repaired-to-mid entry still trades (repair path never gated)",
      len(PLACED) == 1, str(len(PLACED)))
check("D147: repair keeps its CREDITFIX visibility",
      any("CREDIT REPAIRED" in p for p in PUSHES), str(PUSHES))

# ---- D148: filled-only fast scope -----------------------------------------
# (the fast gate needs classify()=='exit' — 'bought back' classifies as an
#  ENTRY verb and took the brain route live; these texts are the D138 shape)
txt = "take gains"
st6 = fresh_st()
st6["open"]["TL1"] = {"id": "TL1", "instrument": "SPXW", "symbol": "SPXW",
                      "status": "pending_entry"}          # unfilled = no exposure
st6["open"]["TL2"] = {"id": "TL2", "instrument": "QQQ", "symbol": "QQQ",
                      "status": "open_tradier"}           # the only FILLED one
got = jp._exit_fast(txt, st6)
check("D148: unfilled entries ignored -> bare exit binds the filled one",
      got == "QQQ", str(got))

st7 = fresh_st()
st7["open"]["TL3"] = {"id": "TL3", "instrument": "QQQ", "status": "open_tradier"}
st7["open"]["TL4"] = {"id": "TL4", "instrument": "SPY", "status": "open_tradier"}
check("D148: 2 FILLED instruments still refuse (never guess)",
      jp._exit_fast(txt, st7) is None, str(jp._exit_fast(txt, st7)))

st8 = fresh_st()
st8["open"]["TL5"] = {"id": "TL5", "instrument": "SPXW", "status": "pending_entry"}
check("D148: all-unfilled book -> None (nothing to bind)",
      jp._exit_fast(txt, st8) is None, str(jp._exit_fast(txt, st8)))

st9 = fresh_st()
st9["open"]["TL6"] = {"id": "TL6", "instrument": "QQQ", "status": "open_tradier"}
check("D148: D138 original single-instrument rule intact",
      jp._exit_fast(txt, st9) == "QQQ", str(jp._exit_fast(txt, st9)))

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
raise SystemExit(0 if not FAILS else 1)
