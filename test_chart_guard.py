"""test_chart_guard.py — msg 4047: a chart image parsed as a 5-leg ticket.

The vision read chart annotations (7378, 57.7%, 1.16x, 7424.4, 64.0) as strikes
and the broker answered "Undefined symbol". Against an index root every strike
must sit in 0.5-1.5x spot.
Run: python test_chart_guard.py   (exit 0 = green)
"""
import james_pipe as jp

FAILS = []
PARKS = []
PLACED = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


jp.save_state = lambda st: None
jp.push = lambda t, s: None
jp.ledger_row = lambda r: None
jp.park_signal = lambda c, m, t, w: PARKS.append(w)
jp._listed = lambda *a: (True, True, None)
jp._under_quote = lambda u: 7415.0


class _F:
    def place(self, rec):
        PLACED.append(rec)

        class R:
            ok = True
            order_id = "T"
            venue = "tradier"
            reason = "stub"
            fill_price = None
        return R()


jp._TRADIER_INST = _F()


def place(strikes, mid="950"):
    PARKS.clear()
    PLACED.clear()
    legs = [{"action": "sell" if i % 2 == 0 else "buy", "strike": float(s),
             "right": "call"} for i, s in enumerate(strikes)]
    norm = {"instrument": "SPXW", "direction": "short", "entry": 6.8,
            "spread": {"type": "credit", "legs": legs},
            "confidence": 55, "horizon": "day", "rationale": "x", "qty": 1}
    jp.open_multileg_tradier(norm, "", mid,
                             {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0})


place([7378.0, 57.7, 16.0, 12.44, 60.0], "951")
check("chart numbers park", bool(PARKS) and not PLACED, str(PARKS))
check("reason names it as a chart", PARKS and "chart" in PARKS[0], str(PARKS))

place([7370.0, 7378.0, 7380.0, 7390.0, 7400.0], "952")
check("a real 5-leg parks on the LEG LIMIT, not on scale",
      bool(PARKS) and "4 per" in PARKS[0], str(PARKS))

# 4-leg whose wings are written 2-digit: anchoring must still land them
place([7370.0, 7380.0, 7365.0, 7365.0], "953")
check("4-leg with 2-digit wings anchors and places",
      bool(PLACED) and not PARKS, f"{PARKS}")
check("wings anchored to the hundreds",
      PLACED and sorted(l.strike for l in PLACED[0].legs)[:2] == [7365.0, 7365.0],
      str([l.strike for l in (PLACED[0].legs if PLACED else [])]))

# >4 legs: the venue builder sends legs[:4], so a fifth would vanish
place([7370.0, 7378.0, 7380.0, 7390.0, 7400.0], "954")
check("5 legs park instead of being truncated",
      bool(PARKS) and not PLACED and "4 per" in PARKS[0], str(PARKS))

# root gate: a chart label is not an option root (4047 "PMH" reached the broker
# along with 3955/3956/3963/3964/3993 as Undefined symbol)
SPOTS = {"SPXW": 7415.0, "SPX500": 7415.0, "WPM": 110.0}
jp._under_quote = lambda u: SPOTS.get(str(u or "").upper())


def run_root(inst, strikes, mid):
    PARKS.clear()
    PLACED.clear()
    legs = [{"action": "sell", "strike": strikes[0], "right": "call"},
            {"action": "buy", "strike": strikes[1], "right": "call"}]
    # D147: entry must clear the spot-derived intrinsic floor by 15% or the
    # stale gate parks the WPM case (floor 5.00 at spot 110 vs entry 3.00 —
    # an impossible price the gate is RIGHT to refuse; 5.4 places).
    jp.open_multileg_tradier(
        {"instrument": inst, "direction": "short", "entry": 5.4,
         "spread": {"type": "credit", "legs": legs}, "confidence": 50,
         "horizon": "day", "rationale": "x"},
        "", mid, {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0})


run_root("PMH", [7378.0, 7390.0], "960")
check("chart label root no longer blocks a readable structure",
      bool(PLACED) or bool(PARKS), str(PARKS))

run_root("SPXW", [7390.0, 7400.0], "961")
check("real index root places", bool(PLACED) and not PARKS, f"{len(PLACED)} {PARKS}")

run_root("WPM", [110.0, 105.0], "962")
check("equity root places", bool(PLACED) and not PARKS, f"{len(PLACED)} {PARKS}")

# ticket wording: "SELL TO OPEN 25 SPXW 7630.000 C" — the 25 is the QUANTITY,
# not a strike (3955/3956/3964 all reached the broker as 4-leg nonsense)
TK = " / ".join(["Bear Call Spread (Credit)",
                 "SELL TO OPEN 25 SPXW 7630.000 C 11/06/26",
                 "BUY TO OPEN 25 SPXW 7650.000 C 11/06/26"])
LG = jp.text_struct(TK)[0]
check("ticket qty is not read as a strike", len(LG) == 2, str(LG))
check("ticket strikes are the real ones",
      LG and [l["strike"] for l in LG] == [7630.0, 7650.0], str(LG))
check("thousands separator stays one number",
      [l["strike"] for l in jp.text_struct(
          "Buy 7,420.60C n Sell 7,424.87C n collect $1")[0]][:2] == [7420.6, 7424.87],
      str(jp.text_struct("Buy 7,420.60C n Sell 7,424.87C n collect $1")[0]))

# a parked image must not lose its levels
ROWS = []
jp.ledger_row = lambda r: ROWS.append(r)
L = jp.capture_chart_levels(
    "O 7,415.0 H 7,427.2 L 7,409.4 Range H 7432.6 L 7368.6 / Buy D 7378.0 +57.7%", "4047")
check("chart levels captured", (7432.6, "above") in L and (7368.6, "below") in L, str(L))
check("buy tag captured", (7378.0, "buy") in L, str(L))
check("levels reach the ledger", any(r.get("event") == "LEVELS" for r in ROWS), str(ROWS[:1]))
L2 = jp.capture_chart_levels("SPX RLM 1.2 SELL 7,420.60 BUY 7,424.87", "3993")
check("dealer list captured", (7420.6, "sell") in L2 and (7424.87, "buy") in L2, str(L2))

# levels must PERSIST, not only print — the ladder has to consume them
jp.LEVELS_F.write_text("[]", encoding="utf-8")
jp.capture_chart_levels("Range H 7432.6 L 7368.6", "4047", None, "SPXW")
kept = jp.recent_levels(8, "SPXW")
check("levels persist to the store", len(kept) == 2, str(kept))
check("store keeps role and level",
      {r["role"] for r in kept} == {"above", "below"}, str([r["role"] for r in kept]))
check("store tags the symbol", all(r["symbol"] == "SPXW" for r in kept), str(kept))
jp.LEVELS_F.write_text("[]", encoding="utf-8")

# ROOT REPAIR: a chart label is not a root, but the strike scale names the venue
run_root("PMH", [7390.0, 7400.0], "970")
check("index-scale root is repaired, not parked", bool(PLACED) and not PARKS,
      f"{len(PLACED)} {PARKS}")
check("repaired root is SPXW", PLACED and PLACED[0].underlying == "SPXW",
      str(PLACED and PLACED[0].underlying))

run_root("PMH", [42.0, 50.0], "971")
check("unreadable scale still parks", bool(PARKS) and not PLACED, str(PARKS))

print(("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED"))
raise SystemExit(0 if not FAILS else 1)
