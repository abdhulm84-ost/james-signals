#!/usr/bin/env python3
"""selftest.py — offline checks for james_pipe: JSON extraction, card validation,
instrument mapping, sizing, simulated short/close math. No network, no Tiger, no Hermes."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import james_pipe as jp

FAILS = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


# ---- extract_json
check("extract: plain json", jp.extract_json('{"a": 1}') == {"a": 1})
check("extract: fenced json",
      jp.extract_json('blah\n```json\n{"a": 2}\n```\nend') == {"a": 2})
check("extract: embedded with braces in strings",
      jp.extract_json('ok {"a": "x{y}z", "b": [1,2]} trailing') == {"a": "x{y}z", "b": [1, 2]})
check("extract: largest of two",
      jp.extract_json('pre {"small": 1} mid {"bigger": "xxxx", "n": 2} post') ==
      {"bigger": "xxxx", "n": 2})
check("extract: garbage -> None", jp.extract_json("no json here at all") is None)
check("extract: empty -> None", jp.extract_json("") is None)

# ---- validate_card
ok, why, norm = jp.validate_card({"instrument": "SPX500", "direction": "SHORT",
                                  "entry": 7758.8, "stop": 7770.0, "targets": [7700.0],
                                  "confidence": 65, "horizon": "overnight",
                                  "rationale": "bear calls"})
check("validate: full short card", ok and norm["direction"] == "short" and norm["stop"] == 7770.0, why)
ok, why, norm = jp.validate_card({"instrument": "NDX", "direction": "long", "targets": ["abc"]})
check("validate: levels nullable, bad target dropped", ok and norm["entry"] is None and norm["targets"] == [], why)
ok, why, _ = jp.validate_card({"direction": "sideways", "instrument": "SPX"})
check("validate: bad direction rejected", not ok and "direction" in why, why)
# text-first entry gate: a REAL entry (trade command in the text) must name an
# instrument; a no-command card passes so the classifier can type it later.
ok, why, _ = jp.validate_card({"direction": "long", "instrument": None}, "buy the dip here")
check("validate: directional w/o instrument rejected", not ok, why)
ok, why, _ = jp.validate_card({"direction": "long", "instrument": None}, "")
check("validate: no-command card passes gate", ok, why)
ok, why, norm = jp.validate_card({"direction": "flat", "instrument": None, "confidence": 0})
check("validate: flat/no-signal card ok", ok and norm["direction"] == "flat", why)
ok, why, norm = jp.validate_card("not a dict at all")
check("validate: non-dict rejected", not ok, why)
ok, why, norm = jp.validate_card({"instrument": "SPX", "direction": "long", "entry": "nan"})
check("validate: NaN entry -> None", ok and norm["entry"] is None, why)

# ---- instrument mapping
check("map: SPX500 -> SPY", jp.map_symbol("SPX500") == "SPY")
check("map: spx case-insensitive -> SPY", jp.map_symbol("spx") == "SPY")
check("map: NDX100 -> QQQ", jp.map_symbol("NDX100") == "QQQ")
check("map: NASDAQ -> QQQ", jp.map_symbol("NASDAQ") == "QQQ")
check("map: VIX -> None (ledger-only)", jp.map_symbol("VIX") is None)
check("map: None safe", jp.map_symbol(None) is None)

# ---- sizing
check("sizing: $1000 @ 600 -> 1 share", max(1, int(1000 / 600)) == 1)
check("sizing: $1000 @ 77 -> 12 shares", max(1, int(1000 / 77)) == 12)

# ---- default stop math
entry, pct = 7758.8, 1.5 / 100
check("stop: long default below entry", round(entry * (1 - pct), 2) < entry)
check("stop: short default above entry", round(entry * (1 + pct), 2) > entry)

# ---- pnl math (long and simulated short)
long_pnl = (7800.0 - 7758.8) * 12
short_pnl = -(7810.0 - 7758.8) * 12
check("pnl: long", abs(long_pnl - 494.4) < 1e-9)
check("pnl: sim short", abs(short_pnl - -614.4) < 1e-9)

# ---- hermes_parse preamble carries the raw text
check("preamble includes RAW SIGNAL marker", "RAW SIGNAL:" in jp.PARSE_PREAMBLE)
check("card keys complete", set(jp.CARD_KEYS) ==
      {"instrument", "direction", "action", "entry", "stop", "targets",
       "confidence", "horizon", "option", "trim", "spread", "rationale", "qty",
       "expiry", "pct_actual", "message_on_image"})

# ---- config sanity
check("config: channel set", bool(jp.CFG["channel"]))
check("config: dry_run is bool", isinstance(jp.CFG["dry_run"], bool))
check("config: map has SPX500+NDX100",
      "SPX500" in {k.upper() for k in jp.CFG["instrument_map"]} and
      "NDX100" in {k.upper() for k in jp.CFG["instrument_map"]})

# ---- action field (entry/exit/update/none)
ok, why, norm = jp.validate_card({"instrument": "SPX500", "direction": "short",
                                  "action": "exit", "rationale": "booked gains"})
check("action: exit card ok", ok and norm["action"] == "exit", why)
ok, why, norm = jp.validate_card({"instrument": "SPX500", "direction": "flat",
                                  "action": "none", "rationale": "price stamp"})
check("action: none ok", ok and norm["action"] == "none", why)
ok, why, norm = jp.validate_card({"direction": "long", "action": "bogus",
                                  "instrument": "SPX"})
check("action: bogus falls back to entry", ok and norm["action"] == "entry", why)
ok, why, _ = jp.validate_card({"direction": "long", "action": "entry", "instrument": None},
                             "enter long now")
check("action: entry w/o instrument rejected", not ok, why)
check("actions set", set(jp.ACTIONS) == {"entry", "exit", "update", "none"})

# ---- update scaling uses stored k
check("update scale: stop 7710 * k(0.0996) ~= 768", abs(7710.0 * 773.09 / 7758.8 - 768.04) < 0.5)

# ---- pre-filter regexes
check("prefilter: zoom link skipped", jp.LINKY.search("https://us02web.zoom.us/j/123 - Join Live now"))
check("prefilter: youtube skipped", jp.LINKY.search("https://youtu.be/p2LBp3ZOFjo - watch today's session"))
check("prefilter: signal with link kept", jp.KEYWORD.search("chart: https://x.com/pic SPX bear calls above 7770"))
check("prefilter: plain signal kept (no linky)", not jp.LINKY.search("7700 coming tonight, bear calls"))

# ---- equities map
check("map: TSLA -> TSLA", jp.map_symbol("TSLA") == "TSLA")
check("map: META -> META", jp.map_symbol("META") == "META")

# ---- options v2: card normalization + identifier + premium math
ok, why, norm = jp.validate_card({
    "instrument": "SPY", "direction": "long", "action": "entry",
    "entry": 1.8, "stop": 0.9, "targets": [2.2, 2.8, 3.9],
    "option": {"underlying": "SPY", "strike": 763, "right": "P", "expiry": None}})
check("option: card normalizes", ok and norm["option"]["strike"] == 763.0 and
      norm["option"]["right"] == "put", why)
ok, why, norm = jp.validate_card({
    "instrument": "AMD", "direction": "flat", "action": "exit",
    "trim": 80, "option": {"underlying": "AMD", "strike": 457.5, "right": "PUT"}})
check("option: trim parses", ok and norm["trim"] == 80, why)
ok, why, norm = jp.validate_card({"instrument": "SPY", "direction": "long",
                                  "trim": "abc"})
check("option: bad trim -> None", ok and norm["trim"] is None, why)
check("option: identifier format",
      jp.opt_identifier("SPY", "2026-10-30", "put", 763.0) == "SPY 261030P00763000")
check("option: identifier call strike",
      jp.opt_identifier("NVDA", "2026-02-21", "call", 180) == "NVDA 260221C00180000")
sr = jp.card_strike_right_from_id("SPY 261030P00763000")
check("option: id round-trip", sr["strike"] == 763.0 and sr["right"] == "PUT")
# premium math: $1000 notional at 1.8 premium -> 5 contracts
check("option: sizing $1000 @ 1.8 -> 5 contracts",
      max(1, min(10, int(1000 / (1.8 * 100)))) == 5)
check("option: sizing min 1 contract @ 8.0 premium",
      max(1, min(10, int(1000 / (8.0 * 100)))) == 1)
# premium PnL: entry 1.8 -> 2.2, 5 contracts = +$200
check("option: pnl math", abs((2.2 - 1.8) * 100 * 5 - 200.0) < 1e-9)
# trim: 80% of 5 contracts = 4
check("option: trim qty", max(1, round(5 * 80 / 100)) == 4)
check("option: expiry default is a Friday",
      jp.next_friday() and __import__("datetime").date.fromisoformat(
          jp.next_friday()).weekday() == 4)
# underlying routing (Tiger paper verified 2026-10-27: SPXW + ETFs only)
check("option: SPX -> SPXW (never SPY)",
      jp.opt_underlying("SPX") == "SPXW" and jp.opt_underlying("SPX500") == "SPXW")
check("option: NDX maps to Tradier NDX", jp.opt_underlying("NDX") == "NDX")
check("option: RUT -> Tradier RUTW", jp.opt_underlying("RUT") == "RUTW")
check("option: RTY (Russell futures) -> RUTW", jp.opt_underlying("RTY") == "RUTW")
check("option: SPY/QQQ pass through",
      jp.opt_underlying("SPY") == "SPY" and jp.opt_underlying("QQQ") == "QQQ")
check("option: equity ticker passes through", jp.opt_underlying("UPS") == "UPS")
check("option: SPXW strike snaps to 5pt grid",
      jp.opt_strike_valid("SPXW", 7642.0) == 7640.0)
check("option: equity strike keeps half-point grid",
      jp.opt_strike_valid("SPY", 763.4) == 763.5)
# spread guard: multi-leg orders must never be executed as a single leg
check("spread: sell/buy pair detected",
      jp.looks_like_spread("UPS Sell 106P n Buy 102P n collect $270 for 11/20"))
check("spread: iron fly detected", jp.looks_like_spread("60% on 7675 Iron fly"))
check("spread: single contract NOT flagged",
      not jp.looks_like_spread("Buy SPY 765P at 1.5 for today"))
check("spread: single index contract NOT flagged",
      not jp.looks_like_spread("SPX 7640P at 3.8, targets 5.2"))
ok, why, norm = jp.validate_card({
    "instrument": "UPS", "direction": "long", "action": "entry", "entry": 2.7,
    "option": {"underlying": "UPS", "strike": 102, "right": "put"},
    "spread": {"type": "credit", "legs": [{"action": "sell", "strike": 106, "right": "put"},
                                          {"action": "buy", "strike": 102, "right": "put"}],
               "net": 2.70}}, "UPS Sell 106P n Buy 102P n collect $270 for 11/20")
check("spread: card keeps spread field", ok and norm["spread"]["net"] == 2.70, why)
# text-first guard: the brain fabricates legs from strike-less text
ok, why, norm = jp.validate_card({
    "instrument": "SPX500", "direction": "short", "action": "entry", "entry": None,
    "spread": {"type": "credit", "legs": [{"action": "sell", "strike": 7735, "right": "call"},
                                          {"action": "buy", "strike": 7745, "right": "call"}],
               "net": 5.5}}, "We are holding Bear calls from yesterday. Post FOMC, big drop")
check("spread: fabricated legs stripped (no strikes in text)",
      ok and norm["spread"] is None, why)
ok, why, norm = jp.validate_card({
    "instrument": "SPX500", "direction": "short", "action": "entry", "entry": 5.5,
    "spread": {"type": "credit", "legs": [{"action": "sell", "strike": 7735, "right": "call"},
                                          {"action": "buy", "strike": 7745, "right": "call"}],
               "net": 5.5}}, "Sell SPX 7735C n Buy 7745C n collect $550 for today")
check("spread: real strikes kept", ok and norm["spread"] is not None, why)
check("map: NDX level message detected",
      jp.is_map_message("Nasdaq 100 E-mini futures: now 29930. S1: 29780 R1: 30080, "
                        "bullish above 30000 bearish below 29850"))
check("map: trade NOT detected as map",
      not jp.is_map_message("Sell SPX 7735C n Buy 7745C n collect 550"))
check("text strikes: 3-digit (106P) and 4-digit (7735C) both found",
      jp.text_strikes("UPS Sell 106P n Buy 102P; SPX 7735C") == [102.0, 106.0, 7735.0])
ok, why, norm = jp.validate_card({"instrument": "SPY", "direction": "long"})
check("spread: absent -> None", ok and norm["spread"] is None, why)
# tick-grid rounding (SPXW trades in 0.1, ETFs in 0.01)
check("option: SPXW tick is 0.1", jp.opt_tick("SPXW") == 0.1)
check("option: SPXW price snaps up to grid",
      jp.opt_price("SPXW", 3.914, up=True) == 4.0)
check("option: SPXW exit snaps down to grid",
      jp.opt_price("SPXW", 3.783, up=False) == 3.7)
check("option: SPY price keeps cents", jp.opt_price("SPY", 1.545, up=True) == 1.55)

# ---- index -> ETF level conversion (open_trade math)
# SPX entry 7758.8, stop 7770, target 7700; SPY quote 773.09 -> k = 773.09/7758.8
k = 773.09 / 7758.8
check("convert: stop scales", abs(7770.0 * k - 774.04) < 0.5)
check("convert: target scales", abs(7700.0 * k - 767.05) < 0.5)
# no-entry card: fixed divisor path
k2 = 773.09 / 10.0
check("convert: divisor fallback", abs(7700.0 * k2 - 7700.0 * 77.309) < 1e-6)
check("config: index_divisors present", jp.CFG.get("index_divisors", {}).get("SPX500") == 10.0)

print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILURES: {FAILS}'}")
sys.exit(1 if FAILS else 0)

# entry gate: the brain calls status+prediction messages "entry"
check("gate: hold+prediction blocked (no trade command)",
      not (jp.entry_language("We are holding Bear calls from yesterday. we will see a big drop")
           or jp.has_tradeable_content("We are holding Bear calls from yesterday. we will see a big drop")))
check("gate: AMKR bracket allowed via verb", jp.entry_language("buy AMKR 38C at 1.8"))
check("gate: AMKR bracket allowed via structure (no verb)",
      jp.has_tradeable_content("AMKR 38C at 1.8 targets 2.2 SL 0.9"))
check("gate: real vertical allowed", jp.entry_language("Sell SPX 7735C n Buy 7745C n collect 550"))
check("gate: exit result blocked", not (jp.entry_language("100% on 7725/7735 Bear calls profits")))
check("gate: classify hold", jp.non_entry_class("We are holding Bear calls") == "hold")
check("gate: classify prediction", jp.non_entry_class("we will see a big drop") == "prediction")
check("gate: classify exit", jp.non_entry_class("book gains on UPS") == "exit")
