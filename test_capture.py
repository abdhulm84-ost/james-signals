"""
test_capture.py — portfolio-screenshot capture + text level capture.

Covers the 2026-11-02 build from the message-by-message exercise (msgs 3551/3552):
  - james_images._normalise passes through a positions[] array
  - text_levels: "7711 is target / if it breaks 7711" -> level store rows
  - portfolio_capture: a 3-row positions screen (James's real book snapshot)
    synthesizes held positions, appends P&L marks, and re-snapshots idempotently

All broker/state side effects are STUBBED. NEVER place a real order from a test.
Run:  python test_capture.py
"""
import james_pipe as jp
import datetime as _dt
_FUTD = jp.us_today() + _dt.timedelta(days=3)
FUT = _FUTD.isoformat()
FUT_TXT = f"{_FUTD.month}/{_FUTD.day}"
from james_images import _normalise

_REAL_HANDLE = jp.handle_exit_update
_REAL_OPT_MARK = jp.opt_mark
_REAL_CLOSE = jp.close_trade

FAILS = []


def check(name, cond, why=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  <- {why}" if (why and not cond) else ""))
    if not cond:
        FAILS.append(name)


jp.save_state = lambda st: None
_SPOTS = {"SPXW": 7700.0, "SPX": 7700.0, "SPX500": 7700.0, "QQQ": 700.0,
          "NDX": 28800.0, "NAS100": 28800.0,
          "SPY": 766.0, "FTNT": 136.0, "EOG": 145.0, "IWM": 245.0,
          "GD": 374.0, "RDDT": 145.0, "NDX100": 28800.0}
jp._under_quote = lambda u: _SPOTS.get(str(u or "").upper(), _SPOTS.get("_default"))
# chains are unknown here by default (snapping has its own block + test_venue)
jp._listed = lambda u, e, r, k: (True, False, None)
jp.spread_credit_mid = lambda u, e, l: None   # text credits: no chain check in tests
_PARKED = []
def _park_spy(card, mid, text, why):
    # D42: CAPTURE ONLY — the old spy forwarded to the real park_signal, so
    # every suite run appended fixture parks (924/928/930/935/942/998/999) into
    # the PRODUCTION parking_lot.csv: msg 928 reached 312 rows.
    if str(mid) in ("920", "921"):
        print("   [park]", mid, "->", why)
    _PARKED.append((mid, why))
jp.park_signal = _park_spy
import tempfile as _tt
_JF = _tt.NamedTemporaryFile(suffix=".csv", delete=False); _JF.close()
jp.JOUT = jp.Path(_JF.name)      # tests never touch the live outcomes file
jp.push = lambda t, s: None
jp.ledger_row = lambda row: None
jp.log = lambda m: None   # keep test noise out of the live james_pipe.log

PLACED = []
CANCELS = []
FAKE = {"status": "filled", "fill": 3.0}


class _FakeTradier:
    def place(self, rec):
        PLACED.append(rec)

        class R:
            ok = True
            order_id = "TESTORDER"
            venue = "tradier"
            reason = "stub"
            fill_price = None
        return R()

    def order_fill(self, oid):
        return FAKE["status"], FAKE["fill"]

    def cancel(self, oid):
        CANCELS.append(oid)
        return FAKE.get("cancel_ok", True), ("cancelled" if FAKE.get("cancel_ok", True) else "HTTP 500")

    def close(self, *a, **k):
        class R:
            ok = True
            order_id = "TESTCLOSE"
            venue = "tradier"
            reason = "stub"
            fill_price = None
        return R()


jp._TRADIER_INST = _FakeTradier()

B = jp.Book()                       # one persistent in-memory book for these tests
jp._load_book = lambda: B
jp._save_book = lambda b: None

# ---- text_levels: the msg-3552 wording ----
lv = jp.text_levels("7711 is target. If it breaks 7711, much bigger drop "
                    "possible but as of now, moving inside the Box.")
check("levels: 7711 target", (7711.0, "target") in lv, str(lv))
check("levels: 7711 break_trigger", (7711.0, "break_trigger") in lv, str(lv))
lv2 = jp.text_levels("SPX bottom is 7695 for today")
check("levels: bottom-is form", (7695.0, "bottom") in lv2, str(lv2))
check("levels: no levels -> empty", jp.text_levels("Nice drop on spx") == [])

# ---- _normalise: positions[] passthrough ----
raw = {"positions": [
    {"instrument": "SPXW 7,745 / 7,755 Calls", "underlying": "SPXW",
     "strikes": [7745, 7755], "expiry": "2026-10-29", "qty": 6,
     "structure": "vertical", "pnl": 360.0},
    {"instrument": "junk row without strikes"},
]}
n = _normalise(raw)
check("normalise: positions kept", isinstance(n.get("positions"), list) and
      len(n["positions"]) == 1, str(n.get("positions")))
r0 = n["positions"][0]
check("normalise: row types", r0["qty"] == 6 and r0["pnl"] == 360.0 and
      r0["strikes"] == [7745.0, 7755.0] and r0["expiry"] == "2026-10-29", str(r0))

# ---- portfolio_capture: James's real msg-3551 screen ----
rows = {"positions": [
    {"instrument": "SPXW 7,745 / 7,755 Calls", "underlying": "SPXW",
     "strikes": [7745, 7755], "expiry": "2026-10-29", "qty": 6,
     "structure": "vertical", "pnl": 360.0},
    {"instrument": "SPXW 7,740 / 7,750 Calls", "underlying": "SPXW",
     "strikes": [7740, 7750], "expiry": "2026-10-29", "qty": 6,
     "structure": "vertical", "pnl": 780.0},
    {"instrument": "SPXW 7,725 / 7,735 Calls", "underlying": "SPXW",
     "strikes": [7725, 7735], "expiry": "2026-10-29", "qty": 10,
     "structure": "vertical", "pnl": 190.0},
]}
# one of the three is already known to the book (the 3548 structure)
B.open("seed", "2026-11-02 00:00:00", structure="vertical", underlying="SPXW",
       body=7745.0, wing_high=7755.0, credit=3.45, qty=6, expiry="2026-10-29")
s = jp.portfolio_capture(rows, "3551", {"seen_msg": {}, "open": {}, "alerts_today": 0})
check("capture: 3 rows", s and s["rows"] == 3, str(s))
check("capture: total pnl 1330", s["total_pnl"] == 1330.0, str(s["total_pnl"]))
check("capture: 3 positions in book", len(B.positions) == 3, str(len(B.positions)))
seed = next(p for p in B.positions.values() if p.body == 7745.0)
check("capture: known pos got pnl mark",
      any(m.get("pnl") == 360.0 for m in seed.marks), str(seed.marks))
synth = next(p for p in B.positions.values() if p.body == 7725.0)
check("capture: synthesized row qty 10", synth.qty == 10 and synth.qty_remaining == 10)

# second snapshot of the same screen: no duplicate positions, mark appended
s2 = jp.portfolio_capture(rows, "3552", {"seen_msg": {}, "open": {}, "alerts_today": 0})
check("capture: re-snapshot adds no positions", len(B.positions) == 3, str(len(B.positions)))
check("capture: re-snapshot appends mark",
      sum(1 for m in seed.marks if m.get("pnl") == 360.0) == 2, str(seed.marks))

check("capture: empty -> None", jp.portfolio_capture({}, "x", {}) is None)

# ---- exit-command precedence (msg 3553: the miss this fixes) ----
from james_book import message_to_op, Op

T3553 = ("Close to 7714 now, All Bear calls we got today and yesterday in nice "
         "gains. Pl book them and we can enter again if it breaks the box")
check("exit-wins: 3553 classifies as exit", jp.non_entry_class(T3553) == "exit")
check("exit-wins: command beats conditional entry verb",
      jp.exit_command_wins(T3553))
check("exit-wins: entry with named legs stays entry",
      not jp.exit_command_wins("Sell SPX 7745C n Buy 7755C n collect $345, book at 50%"))
check("exit-wins: plain entry untouched",
      not jp.exit_command_wins("enter IWM Puts for Monday"))
check("exit-wins: bare status untouched",
      not jp.exit_command_wins("Nice drop on spx"))

check("family: bear calls -> C", jp._structure_family(T3553) == "C")
check("family: bull puts -> P", jp._structure_family("roll the bull puts") == "P")
check("family: none named", jp._structure_family("Nice drop on spx") is None)
legs_call = [(7745.0, "call", "sell"), (7755.0, "call", "buy")]
legs_put = [(7640.0, "put", "sell"), (7630.0, "put", "buy")]
check("fam_ok: call pos IN bear-calls scope", jp._fam_ok({"legs": legs_call}, "C"))
check("fam_ok: put pos NOT in bear-calls scope", not jp._fam_ok({"legs": legs_put}, "C"))
check("fam_ok: stock pos never in structure scope", not jp._fam_ok({"symbol": "SPY"}, "C"))
check("fam_ok: opt_id right filtered",
      jp._fam_ok({"opt_id": "SPXW 261106C00774500"}, "C") and
      not jp._fam_ok({"opt_id": "SPXW 261106P00774500"}, "C"))

trig = jp.text_trigger(T3553)
check("trigger: conditional re-entry captured",
      trig and "if it breaks the box" in trig, str(trig))

check("book op: 'Pl book them' -> CLOSE", message_to_op(T3553, "") == Op.CLOSE)

# ---- gerund gap (msg 3721: "Taking gains on spy" was mis-classified) ----
check("gerund: 'Taking gains on spy' -> exit",
      jp.non_entry_class("Taking gains on spy") == "exit")
check("gerund: exit-wins on it", jp.exit_command_wins("Taking gains on spy"))
check("gerund: book op -> CLOSE", message_to_op("Taking gains on spy", "") == Op.CLOSE)

# ---- strike-scoped trim (msg 3725: only the NAMED structures trim) ----
T3725 = ("7641 done, take gains on 80% of 7625/7635 and 7620 and 7610, "
         "7615/7605, we can hold all others")
named = set(jp.text_strikes(T3725))
check("scope: named strikes parsed",
      {7625.0, 7635.0, 7620.0, 7610.0, 7615.0, 7605.0} <= named, str(named))
check("scope: named legs pass",
      jp._strikes_ok({"legs": [(7625.0, "put", "sell"), (7635.0, "put", "buy")]}, named))
check("scope: unnamed legs fail",
      not jp._strikes_ok({"legs": [(7750.0, "call", "sell"), (7760.0, "call", "buy")]}, named))
check("scope: book pos in list passes",
      jp._strikes_ok({"body": 7620.0, "wing_high": 7610.0}, named))
check("scope: book pos out of list fails",
      not jp._strikes_ok({"body": 7750.0, "wing_high": 7760.0}, named))
check("scope: opt_id strike honoured",
      jp._strikes_ok({"opt_id": "SPXW 261106P07625000"}, named) and
      not jp._strikes_ok({"opt_id": "SPXW 261106P07750000"}, named))
check("scope: stock never strike-scoped", not jp._strikes_ok({"symbol": "SPY"}, named))
check("scope: no named strikes -> all pass",
      jp._strikes_ok({"body": 7750.0, "wing_high": 7760.0}, set()))

B3 = jp.Book()
q1 = B3.open("a1", "2026-11-02 00:00:00", structure="bull_put_spread", underlying="SPX",
             body=7625.0, wing_high=7635.0, qty=6)
q2 = B3.open("a2", "2026-11-02 00:00:00", structure="bull_put_spread", underlying="SPX",
             body=7620.0, wing_high=7610.0, qty=6)
q3 = B3.open("a3", "2026-11-02 00:00:00", structure="bull_put_spread", underlying="SPX",
             body=7615.0, wing_high=7605.0, qty=6)
q4 = B3.open("a4", "2026-11-02 00:00:00", structure="bull_put_spread", underlying="SPX",
             body=7750.0, wing_high=7760.0, qty=6)
jp._load_book = lambda: B3
jp._save_book = lambda b: None
jp.book_apply({"instrument": "SPX500", "direction": "short"}, T3725, "3725", {})
check("trim scope: named pos 1 trimmed", q1.state.value == "trimmed", q1.state.value)
check("trim scope: named pos 2 trimmed", q2.state.value == "trimmed", q2.state.value)
check("trim scope: named pos 3 trimmed", q3.state.value == "trimmed", q3.state.value)
check("trim scope: UNNAMED pos untouched (7750/7760)",
      q4.state.value == "open" and q4.qty_remaining == 6, f"{q4.state.value}/{q4.qty_remaining}")

# ---- lotto: no-verb slash spread, 2-digit anchor, entry routing ----
TLOT = "Spx lotto 50/60 bull puts for today- take small"
check("lotto: no-verb legs parsed (sell the HIGHER strike for a bull put)",
      jp.text_spread(TLOT) == ([{"action": "sell", "strike": 60.0, "right": "put"},
                                {"action": "buy", "strike": 50.0, "right": "put"}], []),
      str(jp.text_spread(TLOT)))
check("lotto: no inconsistency flag", "inconsistent" not in jp.text_struct(TLOT)[1])
check("lotto: still an ENTRY (not exit)", not jp.exit_command_wins(TLOT))
check("strike-exit: 'pl book 7745/7755 bear calls' is an exit",
      jp.exit_command_wins("pl book 7745/7755 bear calls"))

jp.bbc = lambda: type("B", (), {"get_quote": staticmethod(lambda inst: 7545.0)})()
PLACED.clear()
stL = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
normL = {"instrument": "SPX", "direction": "long", "entry": None, "spread": None,
         "confidence": 50, "horizon": "day", "rationale": "lotto", "qty": 5}
jp.open_multileg_tradier(normL, TLOT, "914", stL)
rec = PLACED[0] if PLACED else None
check("lotto place: legs anchored to 7550/7560",
      rec and {(l.strike, l.action) for l in rec.legs} ==
      {(7550.0, "buy"), (7560.0, "sell")},
      str([(l.strike, l.action) for l in (rec.legs if rec else [])]))
check("lotto place: qty forced to 1 despite image qty 5", rec and rec.qty == 1)
check("lotto place: books SHORT (sold leg)", rec and rec.direction == "short")

# ---- vision P&L scale guard (msg 3729: 632.00 read as 63200) ----
n2 = _normalise({"pnl": -63200})
n3 = _normalise({"pnl": 69400, "positions": [{"strikes": [7630],
                                              "underlying": "SPXW", "pnl": 69400}]})
n4 = _normalise({"pnl": 360})
check("scale: top-level 63200 -> 632", n2.get("pnl") == -632.0, str(n2.get("pnl")))
check("scale: row 69400 -> 694", n3["positions"][0].get("pnl") == 694.0,
      str(n3["positions"][0].get("pnl")))
check("scale: normal pnl untouched", n4.get("pnl") == 360.0, str(n4.get("pnl")))

# ---- book side: 'all Bear calls' closes ONLY the bear calls ----
B2 = jp.Book()
p1 = B2.open("s1", "2026-11-02 00:00:00", structure="bear_call_spread",
             underlying="SPX", body=7745.0, wing_high=7755.0, qty=6)
p2 = B2.open("s2", "2026-11-02 00:00:00", structure="bull_put_spread",
             underlying="SPX", body=7640.0, wing_high=7630.0, qty=1)
jp._load_book = lambda: B2
jp._save_book = lambda b: None
jp.book_apply({"instrument": "SPX500", "direction": "short"}, T3553, "3553", {})
check("book: bear call CLOSED by 'book them'", not p1.is_open)
check("book: bull put untouched by 'bear calls' scope", p2.is_open)

# ---- end-to-end routing: msg 3553 goes to the EXIT path ----
calls = []
jp.hermes_parse = lambda text, photo=None: {"instrument": "SPX500",
                                            "direction": "short",
                                            "action": "none", "rationale": "x"}
jp.handle_exit_update = lambda act, norm, msg_id, st, text="": calls.append(
    (act, msg_id))
jp._load_book = lambda: B
jp._save_book = lambda b: None
jp._parse_trade(T3553, "3553", None, {"seen_msg": {}, "open": {}, "alerts_today": 0}, 0)
check("routing: 3553 -> EXIT path", calls == [("exit", "3553")], str(calls))

# ---- audit trail: reason never comes from a neighbour message ----
check("reason: matching rationale kept",
      jp.reason_text({"rationale": "nice drop on spx"}, "Nice drop on spx again")
      == "nice drop on spx")
check("reason: neighbouring text -> raw fallback",
      jp.reason_text({"rationale": "5.5k to go for 2k to 50k"},
                     "Close to 7714 now, All Bear calls").startswith("Close"))

# ---- level variants: '7660 next target' ----
lv3760 = jp.text_levels("7660 next target to close all remaining Bull Puts")
check("levels: next-target form", (7660.0, "next_target") in lv3760, str(lv3760))

# ---- junk snapshot rows skipped ----
B4 = jp.Book()
jp._load_book = lambda: B4
jp._save_book = lambda b: None
junkres = jp.portfolio_capture({"positions": [
    {"instrument": "SPXW 7,625 / 7,635 Puts", "underlying": "SPXW",
     "strikes": [7625, 7635], "qty": 6, "pnl": 100.0},
    {"instrument": "SPXW 7630.000 P", "underlying": "SPXW",
     "strikes": [7630], "qty": 10, "pnl": -632.0},
]}, "9j", {"seen_msg": {}, "open": {}, "alerts_today": 0})
check("rows: unpaired single leg kept as a single",
      junkres["rows"] == 2 and junkres["skipped"] == 0, str(junkres))

# ---- leg-sheet pairing (3568/3569/3570 shape: one row per leg) ----
B6 = jp.Book()
jp._load_book = lambda: B6
jp._save_book = lambda b: None
jp.portfolio_capture({"positions": [
    {"instrument": "GOOG 310.000 C", "underlying": "GOOG", "strikes": [310],
     "qty": -5, "pnl": 9455, "expiry": "2026-11-20"},
    {"instrument": "GOOG 315.000 C", "underlying": "GOOG", "strikes": [315],
     "qty": 5, "pnl": -8135, "expiry": "2026-11-20"}]}, "9L", {})
pG = next(iter(B6.positions.values()))
check("pair: GOOG legs -> ONE bear call spread",
      len(B6.positions) == 1 and pG.structure == "bear_call_spread", pG.structure)
check("pair: qty 5, net pnl 1320, strikes 310/315",
      pG.qty == 5 and pG.marks[-1]["pnl"] == 1320.0 and
      pG.body == 310.0 and pG.wing_high == 315.0)
jp.portfolio_capture({"positions": [
    {"instrument": "SPXW 7685.000 C", "underlying": "SPXW", "strikes": [7685],
     "qty": -5, "pnl": 19810, "expiry": "2026-10-29"},
    {"instrument": "SPXW 7690.000 C", "underlying": "SPXW", "strikes": [7690],
     "qty": 5, "pnl": -18435, "expiry": "2026-10-29"}]}, "9m", {})
pS = next(p for p in B6.positions.values() if p.body == 7685.0)
check("pair: 4-digit index legs pair too",
      pS.structure == "bear_call_spread" and pS.qty == 5)
jp.portfolio_capture({"positions": [
    {"instrument": "SPXW 7620.000 P", "underlying": "SPXW", "strikes": [7620],
     "qty": -6, "expiry": "2026-10-29"},
    {"instrument": "SPXW 7610.000 P", "underlying": "SPXW", "strikes": [7610],
     "qty": 6, "expiry": "2026-10-29"}]}, "9n", {})
pP = next(p for p in B6.positions.values() if p.body == 7610.0)
check("pair: put sheet -> bull put spread",
      pP.structure == "bull_put_spread" and pP.qty == 6)

# ---- 3570: bare '<n>% on <family>' is a STATUS (D121), not an exit ------
# Its own ledger row is NARRATIVE (never traded): 'Live if anyone got it' is a
# claim about HOLDERS, not a command to close. A bare % with no action verb
# must not route to the exit path (4496 full-closed a winner on this shape).
T3570 = "70% on SPX bear calls from yesterday Live if anyone got it"
check("3570: bare % is NOT an exit (D121 status)", jp.non_entry_class(T3570) != "exit",
      jp.non_entry_class(T3570))
check("3570: exit-wins False (D121)", not jp.exit_command_wins(T3570))
# ...but the SCOPE still resolves, so if a verb is added the same message routes
check("3570: family C + ticker SPX",
      jp.scope_family(T3570) == "C" and jp.scope_instrument(T3570) == "SPX")
check("3570: same text WITH an action verb routes (D121 keeps commands)",
      jp.exit_command_wins("70% on SPX bear calls from yesterday, book gains"))

# ---- instrument reconcile: text caps token beats a hallucinated brain label ----
okI, _, normI = jp.validate_card(
    {"instrument": "SLV", "direction": "flat", "action": "exit"},
    "95% on GOOG bear calls, Pl take gains so we can release the money for other trades")
check("reconcile: GOOG from text beats brain SLV",
      okI and normI.get("instrument") == "GOOG", str(normI.get("instrument")))
okJ, _, normJ = jp.validate_card(
    {"instrument": "SPX500", "direction": "flat", "action": "exit"},
    "Take gains on remaining Bull Puts if anyone holding, holding Bear calls")
check("reconcile: no caps token -> brain instrument kept",
      okJ and normJ.get("instrument") == "SPX500")
okK, _, normK = jp.validate_card(
    {"instrument": "SPX500", "direction": "flat", "action": "none"},
    "SLV, GLD, QQQ, SPX and CL all moving")
check("reconcile: multiple tickers -> no override",
      okK and normK.get("instrument") == "SPX500")

# ---- exit ladder: parse + arm + hit ----
def _bb_quote(px):
    jp.bbc = lambda: type("B", (), {
        "get_quote": staticmethod(lambda inst: float(px))})()

pc = jp.parse_target_close("7660 next target to close all remaining Bull Puts")
check("ladder: parse level+scope", pc == (7660.0, "all remaining Bull Puts"), str(pc))
check("ladder: 'close all X at N' form",
      jp.parse_target_close("close all QQQ puts at 3.2") == (3.2, "QQQ puts"))
stT = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
posT = {"id": "TL9", "instrument": "SPXW", "status": "open_tradier", "direction": "short",
        "legs": [(7600.0, "put", "sell"), (7590.0, "put", "buy")]}
stT["open"]["TL9"] = posT
check("ladder: message handled",
      jp.arm_exit_targets({"instrument": "SPX500"},
                          "7660 next target to close all remaining Bull Puts",
                          "3760", stT))
check("ladder: target armed above", posT["exit_target"]["level"] == 7660.0 and
      posT["exit_target"]["side"] == "above")
_bb_quote(7659.0)
check("ladder: 7659 no hit", jp._exit_target_hit(posT)[0] is False)
_bb_quote(7661.0)
check("ladder: 7661 hit", jp._exit_target_hit(posT)[0] is True)

# ---- screenshot expiry reaches the SPREAD path (msg 3562: text had no date,
#      screenshot was 0DTE; the resolver would have used next Friday) ----
cardE = {"instrument": "SPX", "direction": "long", "action": "entry", "entry": 3.7,
         "spread": {"type": "credit", "legs": [
             {"action": "sell", "strike": 7725.0, "right": "call"},
             {"action": "buy", "strike": 7735.0, "right": "call"}]}}
jp._merge_img_into_card(cardE, {"expiry": "2026-12-04", "qty": -6})
check("expiry: image expiry merged onto the card", cardE.get("expiry") == "2026-12-04")
okE, whyE, normE = jp.validate_card(
    cardE, "Sell SPX 7725C n Buy 7735C n collect $370 to $380")
check("expiry: survives validation", okE and normE.get("expiry") == "2026-12-04", whyE)
PLACED.clear()
stE = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
jp.open_multileg_tradier(normE,
                         "Sell SPX 7725C n Buy 7735C n collect $370 to $380", "3562", stE)
check("expiry: spread uses the screenshot date, not next Friday",
      PLACED and getattr(PLACED[0], "expiry", None) == "2026-12-04",
      str(getattr(PLACED[0], "expiry", None) if PLACED else None))
check("expiry: credit takes the range LOWER bound",
      PLACED and PLACED[0].credit == 3.7, str(PLACED[0].credit if PLACED else None))
check("expiry: qty from screenshot halves then caps at 1 (D72)",
      PLACED and PLACED[0].qty == 1, str(PLACED[0].qty if PLACED else None))

# ---- 3565/3566/3567: verb-anchored family, range trim, '% on' = full close ----
T3565 = "Take gains on remaining Bull Puts if anyone holding, holding Bear calls"
T3566 = "Take gains on bear calls 2t/35"
T3567 = "95% on QQQ Bear calls, Pl book gains"
check("family: 3565 scopes to BULL PUTS despite held bear calls",
      jp.scope_family(T3565) == "P", str(jp.scope_family(T3565)))
check("family: 3566 -> C", jp.scope_family(T3566) == "C")
check("family: 3567 -> C", jp.scope_family(T3567) == "C")
check("ticker: 3567 text ticker beats brain label",
      jp.scope_instrument(T3567) == "QQQ" and jp.scope_instrument(T3565) is None)
check("frac: '2t/35' -> 25% trim", jp.parse_exit_directive({}, T3566)[2] == 0.25,
      str(jp.parse_exit_directive({}, T3566)[2]))
check("frac: '95% on ... book gains' -> FULL close",
      jp.parse_exit_directive({}, T3567)[2] == 1.0,
      str(jp.parse_exit_directive({}, T3567)[2]))
check("frac: 'trim 80% and hold 20%' still 0.8",
      jp.parse_exit_directive({}, "trim 80% and hold 20%")[2] == 0.8)
check("book op: 3565 CLOSE", message_to_op(T3565, "") == Op.CLOSE)
check("book op: 3566 TRIM", message_to_op(T3566, "") == Op.TRIM)
check("book op: 3567 CLOSE", message_to_op(T3567, "") == Op.CLOSE)

B5 = jp.Book()
bp5 = B5.open("b5", "2026-11-03 00:00:00", structure="bull_put_spread",
              underlying="SPX", body=7620.0, wing_high=7610.0, qty=6)
bc5 = B5.open("b5c", "2026-11-03 00:00:00", structure="bear_call_spread",
              underlying="SPX", body=7745.0, wing_high=7755.0, qty=6)
jp._load_book = lambda: B5
jp._save_book = lambda b: None
jp.book_apply({"instrument": "SPX500", "direction": "short"}, T3565, "3565", {})
check("3565: bull put closed", not bp5.is_open)
check("3565: bear call HELD (named later in the message)", bc5.is_open)

# junk guard must NOT skip 3-digit equity singles (QQQ 720C leg sheets)
junkres2 = jp.portfolio_capture({"positions": [
    {"instrument": "QQQ 720.000 C", "underlying": "QQQ", "strikes": [720], "qty": 5},
]}, "9k", {"seen_msg": {}, "open": {}, "alerts_today": 0})
check("junk: 3-digit equity single kept",
      junkres2["rows"] == 1 and junkres2["skipped"] == 0, str(junkres2))

# ---- 3571 (TXN trim 80%): sheet captured, trim hits TXN bear calls only ----
B7 = jp.Book()
jp._load_book = lambda: B7
jp._save_book = lambda b: None
jp.portfolio_capture({"positions": [
    {"instrument": "TXN 305.000 C", "underlying": "TXN", "strikes": [305],
     "qty": -5, "pnl": 215.0, "expiry": FUT},
    {"instrument": "TXN 310.000 C", "underlying": "TXN", "strikes": [310],
     "qty": 5, "pnl": 130.0, "expiry": FUT}]}, "3571a", {})
pT = next(iter(B7.positions.values()))
check("txn: sheet paired to bear call qty 5",
      pT.structure == "bear_call_spread" and pT.qty == 5)
other = B7.open("o", "x", structure="bull_put_spread", underlying="SPX",
                body=7620.0, wing_high=7610.0, qty=6)
jpa = jp.message_to_op("TXN bear calls in nice gains, Trim 80%", "")
check("txn: 'Trim 80%' -> TRIM op", jpa.value == "trim", jpa.value)
jp.book_apply({"instrument": "TXN", "direction": "short"},
              "TXN bear calls in nice gains, Trim 80%", "3571", {})
check("txn: 80% trimmed (5 -> 1 remaining)", pT.qty_remaining == 1,
      str(pT.qty_remaining))
check("txn: state trimmed", pT.state.value == "trimmed", pT.state.value)
check("txn: other underlying untouched", other.qty_remaining == 6 and other.is_open)

# ---- 3572 (CVNA 62C bracket): text-wins forces an ENTRY, 62 stays 62 ----
PLACED.clear()
jp.hermes_parse = lambda text, photo=None: {
    "instrument": "SPX500", "direction": "short", "action": "exit", "entry": None,
    "stop": None, "targets": [], "confidence": 50, "horizon": "", "rationale": "x",
    "trim": None, "spread": None,
    "option": {"underlying": "CVNA", "strike": 62, "right": "call",
               "expiry": "2026-10-30"}}
jcalls = []
jp.handle_exit_update = lambda act, norm, msg_id, st, text="": jcalls.append(act)
stZ = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
jp._parse_trade("buy CVNA 62C at 5.4 for 10/30 / Targets 6.4/8.2/11.4 / SL 2.7",
                "3572", None, stZ, 0)
check("cvna: forced to ENTRY not exit (the 10/29 miss)", jcalls == [], str(jcalls))
# placement with a live expiry (the audit runs after 10/30)
PLACED.clear()
cardC = {"direction": "long", "action": "entry", "entry": 5.4, "stop": 2.7,
         "targets": [6.4, 8.2, 11.4], "confidence": 80, "horizon": "swing",
         "rationale": "cvna bracket", "option": {"underlying": "CVNA",
                                                 "strike": 62, "right": "call",
                                                 "expiry": FUT}}
jp.open_option_trade(cardC, "3572b", {"seen_msg": {}, "open": {}, "alerts_today": 0},
                     "buy CVNA 62C at 5.4 Targets 6.4/8.2/11.4 SL 2.7")
check("cvna: option order placed", len(PLACED) == 1, str(len(PLACED)))
check("cvna: strike stays 62 (no index anchor)",
      PLACED and PLACED[0].legs[0].strike == 62.0,
      str(PLACED[0].legs[0].strike if PLACED else None))
check("cvna: long call, qty 1", PLACED and PLACED[0].direction == "long" and
      PLACED[0].qty == 1)

# ---- 3576: '7690 target again' level form ----
lv76 = jp.text_levels("Price broke the box so Box bottom will act as Resistance for SPX, 7690 target again")
check("levels: 'N target again' captured", (7690.0, "target") in lv76, str(lv76))

# ---- order-ticket extraction merge path (3574 GOOG / 3575 SPXW tickets) ----
cardT = {"instrument": "GOOG", "direction": "long", "action": "entry", "entry": None,
         "stop": None, "targets": [], "spread": {"type": "credit", "legs": [
             {"action": "sell", "strike": 287.5, "right": "put"},
             {"action": "buy", "strike": 282.5, "right": "put"}]}}
jp._merge_img_into_card(cardT, {"underlying": "GOOG", "qty": 5, "credit": 2.73,
                                "fill_price": 2.73, "strikes": [282.5, 287.5],
                                "structure": "vertical"})
okT, _, normT = jp.validate_card(
    cardT, "Sell GOOG 287.5P n Buy 282.5P n collect $273 for 10/30")
check("ticket: qty/credit merged", okT and normT.get("qty") == 5 and
      normT.get("entry") == 2.73, f"{normT.get('qty')}/{normT.get('entry')}")

# ---- quality sizing: thin flyer halves, good quality keeps size ----
NORMQ = {"instrument": "QQQ", "direction": "long", "entry": 0.61, "qty": 5,
         "spread": {"type": "credit", "legs": [
             {"action": "sell", "strike": 716.0, "right": "call"},
             {"action": "buy", "strike": 718.0, "right": "call"}]},
         "confidence": 60, "horizon": "day", "rationale": "x"}
PLACED.clear()
_SPOTS["QQQ"] = 700.0                      # calls 716/718 sit OTM above spot
jp.open_multileg_tradier(dict(NORMQ),
                         f"Sell QQQ 716C n Buy 718C n collect $61 for {FUT_TXT}",
                         "920", {"seen_msg": {}, "open": {}, "closed": {},
                                 "alerts_today": 0})
check("sizing: 30.5% quality halves 5 -> 2, capped at 1 (D72)",
      PLACED and PLACED[0].qty == 1, str(PLACED[0].qty if PLACED else None))
NORMQ2 = {**NORMQ, "entry": 6.0,
          "spread": {"type": "credit", "legs": [
              {"action": "sell", "strike": 716.0, "right": "put"},
              {"action": "buy", "strike": 706.0, "right": "put"}]}}
PLACED.clear()
_SPOTS["QQQ"] = 720.0                      # puts 716/706 sit OTM below SPOT
jp.open_multileg_tradier(dict(NORMQ2),
                         f"Sell QQQ 716P n Buy 706P n collect $600 for {FUT_TXT}",
                         "921", {"seen_msg": {}, "open": {}, "closed": {},
                                 "alerts_today": 0})
_SPOTS["QQQ"] = 700.0
check("sizing: 60% quality keeps 5, capped at 1 (D72)",
      PLACED and PLACED[0].qty == 1, str(PLACED[0].qty if PLACED else None))

# ---- prediction auto-settle ----
import tempfile
import os
tf = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                 encoding="utf-8", newline="")
tf.write("date,msg_ref,prediction,type,level,claimed,actual_outcome,result,notes,,verified\n")
tf.write("2026-11-02,3704,SPX above 7575 EOD,level,SPX > 7575 EOD,>7575,,open,,,\n")
tf.close()
jp.PRED_FILE = jp.Path(tf.name)
_bb_quote(7700.0)
jpr = {"seen_msg": {}, "open": {}, "alerts_today": 0}
jp.settle_predictions(jpr)
body = open(tf.name, encoding="utf-8").read().splitlines()[1]
check("settle: level row resolved correctly", ",correct," in body, body)
os.unlink(tf.name)
jp.PRED_FILE = jp.Path(r"C:\Trading\james-signals\james_predictions.csv")

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    raise SystemExit(1)
print("ALL PASS")

# ---- multileg FILL VERIFICATION: acceptance is not a fill ----
# D40 (clock family): the expiry comes from this text — pin it to the
# runtime clock ("for 11/21" went stale when the US date rolled to 11-22
# and the placement was refused, so no TL918/TL919 ever existed).
TSPR = f"Sell SPX 7745C n Buy 7755C n collect $345 for {FUT_TXT}"
normF = {"instrument": "SPX", "direction": "short", "entry": 3.45, "qty": 1,
         "spread": {"type": "credit", "legs": [
             {"action": "sell", "strike": 7745.0, "right": "call"},
             {"action": "buy", "strike": 7755.0, "right": "call"}]},
         "confidence": 70, "horizon": "day", "rationale": "x"}

FAKE.update(status="open", fill=None)
stF = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
jp.open_multileg_tradier(dict(normF), TSPR, "918", stF)
tF = stF["open"].get("TL918", {})
check("fill: unfilled placement -> pending_entry", tF.get("status") == "pending_entry",
      str(tF.get("status")))

FAKE.update(status="filled", fill=3.3)
jp.manage_once(stF)
check("fill: promote on fill with REAL entry", tF.get("status") == "open_tradier" and
      tF.get("entry") == 3.3, f"{tF.get('status')}/{tF.get('entry')}")

FAKE.update(status="open", fill=None)
tF["status"] = "pending_entry"
tF["opened"] = "2020-01-01 00:00:00"
CANCELS.clear()
# the stale-cancel branch sits BEHIND the D04 market-open queue guard: with the
# venue shut (this file runs at any wall-clock time) a pending just queues and
# the cancel never fires. Stub the venue open so the branch under test runs —
# same pattern as test_stale.py.
_REAL_MO = jp.market_open_now
jp.market_open_now = lambda: True
jp.manage_once(stF)
jp.market_open_now = _REAL_MO
check("fill: stale pending cancelled, no position",
      tF.get("status") == "closed" and tF.get("pnl") == 0.0 and
      "TL918" not in stF["open"] and CANCELS == ["TESTORDER"],
      f"{tF.get('status')}/{CANCELS}")

# exit while still pending cancels the resting entry
FAKE.update(status="open", fill=None)
stH = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
jp.open_multileg_tradier(dict(normF), TSPR, "919", stH)
tH = stH["open"]["TL919"]
CANCELS.clear()
jp.close_trade(tH, 0.0, "test exit", stH)
check("fill: exit on unfilled entry cancels, PnL 0",
      tH.get("status") == "closed" and tH.get("pnl") == 0.0 and
      CANCELS == ["TESTORDER"] and "TL919" not in stH["open"],
      f"{tH.get('status')}/{CANCELS}")
FAKE.update(status="filled", fill=3.0)

# ---- no-ticker strike-scale guard (msg 3579 pattern) ----
def _st_scale():
    return {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}


NORMNT = {"instrument": "QQQ", "direction": "long", "entry": 4.5, "qty": 2,
          "spread": {"type": "credit", "legs": [
              {"action": "sell", "strike": 7720.0, "right": "call"},
              {"action": "buy", "strike": 7730.0, "right": "call"}]},
          "confidence": 60, "horizon": "day", "rationale": "x"}
PLACED.clear()
jp.open_multileg_tradier(dict(NORMNT),
                         f"Sell 7720C n Buy 7730C n collect $450 for {FUT_TXT}",
                         "922", _st_scale())
check("scale: no ticker + 4-digit strikes -> SPXW (brain QQQ overridden)",
      PLACED and PLACED[0].underlying == "SPXW",
      str(PLACED[0].underlying if PLACED else None))

NORMND = dict(NORMNT)
NORMND["spread"] = {"type": "credit", "legs": [
    {"action": "sell", "strike": 29000.0, "right": "call"},
    {"action": "buy", "strike": 29050.0, "right": "call"}]}
PLACED.clear()
jp.open_multileg_tradier(dict(NORMND),
                         f"Sell 29000C n Buy 29050C n collect $450 for {FUT_TXT}",
                         "923", _st_scale())
check("scale: no ticker + 5-digit strikes -> NDX",
      PLACED and PLACED[0].underlying == "NDX",
      str(PLACED[0].underlying if PLACED else None))

NORMEQ = dict(NORMNT)
NORMEQ["instrument"] = "SPX500"
NORMEQ["spread"] = {"type": "credit", "legs": [
    {"action": "sell", "strike": 716.0, "right": "put"},
    {"action": "buy", "strike": 706.0, "right": "put"}]}
PLACED.clear()
jp.open_multileg_tradier(dict(NORMEQ),
                         f"Sell 716P n Buy 706P n collect $300 for {FUT_TXT}",
                         "924", _st_scale())
check("scale: no ticker + 3-digit strikes under an index root -> park (no order)",
      len(PLACED) == 0)

PLACED.clear()
jp.open_multileg_tradier(dict(NORMNT),
                         f"Sell QQQ 716C n Buy 718C n collect $61 for {FUT_TXT}",
                         "925", _st_scale())
check("scale: a text ticker always wins (QQQ kept)",
      PLACED and PLACED[0].underlying == "QQQ",
      str(PLACED[0].underlying if PLACED else None))

# ---- 3581: mark messages carry the painted % (not 0) ----
T3581 = ("Nice drop of 18 points on SPX again, Back to 7705 from 7723, "
         "7720/7730 In Nice gains one more time")
check("3581: narrative (mark), not an exit",
      jp.non_entry_class(T3581) == "narrative" and not jp.exit_command_wins(T3581))
okM, _, normM = jp.validate_card(
    {"instrument": "NDX100", "direction": "long", "action": "none"},
    T3581)
check("3581: instrument NDX100 -> SPX", okM and normM.get("instrument") == "SPX",
      str(normM.get("instrument")))
normM["pct_actual"] = 40
B8 = jp.Book()
p8 = B8.open("m8", "x", structure="bear_call_spread", underlying="SPX",
             body=7720.0, wing_high=7730.0, qty=6)
jp._load_book = lambda: B8
jp._save_book = lambda b: None
jp.book_apply(normM, T3581, "3581", {})
check("3581: mark stored the 40%", p8.marks and p8.marks[-1].get("pct") == 40,
      str(p8.marks))
check("3581: still open (a mark is not a close)", p8.is_open)

# ---- 3581 follow-up: a mark refreshes qty from the card + flags accounts ----
B9 = jp.Book()
p9 = B9.open("m9", "x", structure="bear_call_spread", underlying="SPX",
             body=7720.0, wing_high=7730.0, qty=10)
jp._load_book = lambda: B9
jp._save_book = lambda b: None
norm9 = {"instrument": "SPX", "direction": "long", "action": "none", "qty": -6,
         "pct_actual": 40}
jp.book_apply(norm9, "Nice gains one more time", "3581b", {})
check("qty refresh: 10 -> 6 from the card", p9.qty == 6 and p9.qty_remaining == 6,
      str(p9.qty))
check("qty refresh: account note added",
      "account unknown" in (p9.note or ""), p9.note)
check("qty refresh: mark still stored, position open",
      p9.marks and p9.marks[-1].get("pct") == 40 and p9.is_open)

# ---- single TRADIER option: exit routes to the tradier close, not Tiger ----
_cB = []
jp.close_trade = lambda t, px, why, st: _cB.append((t["id"], why))
jp.close_option = lambda t, px, q, why, st: _cB.append(("OPT:" + t["id"], why))
t396 = {"id": "O9", "executor": "tradier", "status": "open_tradier",
        "instrument": "BMRN 60C", "symbol": "BMRN", "direction": "long",
        "qty": 2, "entry": 1.3, "targets": [], "stop": None}
EA = type("E", (), {"kind": type("K", (), {"value": "exit"})(),
                    "close_fraction": 1.0, "limit_price": None})()
jp.execute_exit_action(EA, [t396], {}, "9", {})
check("opt exit: TRADIER close path, not close_option",
      _cB and _cB[0][0] == "O9", str(_cB))

# ---- filled tradier single: bracket hit closes it ----
_stB = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
jp.opt_mark = lambda t: 1.6
t397 = {"id": "O10", "executor": "tradier", "status": "open_tradier",
        "opt_id": "BMRN 261120C00060000", "instrument": "BMRN 60C",
        "symbol": "BMRN", "direction": "long", "qty": 1, "entry": 1.3,
        "targets": [1.5], "stop": 0.9}
_stB["open"]["O10"] = t397
_cB.clear()
jp.manage_once(_stB)
check("opt bracket: target hit routes a close", _cB and _cB[0][0] == "O10",
      str(_cB))

# ---- overnight fixes (msg 3769 blind close + TL3765 orphan class) ----
jp.handle_exit_update = _REAL_HANDLE
# 1) unnamed exit scoped by caps tickers closes only those
_cC = []
jp.close_trade = lambda t, px, why, st: _cC.append(t["id"])
jp.close_option = lambda t, px, q, why, st: _cC.append("OPT:" + t["id"])
stC = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
stC["open"]["F1"] = {"id": "F1", "instrument": "FISV 52C", "symbol": "FISV",
                     "executor": "tradier", "status": "open_tradier",
                     "opt_id": "FISV 261106C00052000", "direction": "long",
                     "expiry": FUT, "qty": 1, "entry": 1.0}
stC["open"]["BLK1"] = {"id": "BLK1", "instrument": "BLK", "symbol": "BLK",
                       "executor": "tradier", "status": "open_tradier",
                       "direction": "short", "qty": 1, "entry": 4.8}
jp.handle_exit_update("exit", {"instrument": None, "direction": "short"},
                      "3769", stC,
                      "Nice gains with CLX, FISV, Trim 80% on FISV")
check("3769: only the named ticker closes", _cC == ["F1"], str(_cC))

# 2) unnamed exit with NO scope at all -> refuse, close nothing
_cC.clear()
jp.handle_exit_update("exit", {"instrument": None, "direction": "short"},
                      "x", stC, "pl book gains")
check("no-scope exit refused", _cC == [], str(_cC))

# 3) cancel FAILED on unfilled exit -> position stays pending, never booked closed
FAKE.update(cancel_ok=False)
stD2 = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
tD = {"id": "P1", "executor": "tradier", "status": "pending_entry",
      "instrument": "SPXW", "symbol": "SPXW", "direction": "short",
      "order_id": "OID1", "entry": 3.9, "qty": 1}
stD2["open"]["P1"] = tD
jp.close_trade(tD, 0.0, "exit msg", stD2)
check("cancel-failed: stays pending (orphan class)",
      tD["status"] == "pending_entry" and "P1" in stD2["open"],
      tD["status"])
FAKE.update(cancel_ok=True)

# 4) debit spread books LONG (D147: entry must clear the spot floor — spot
# 7700 puts 7570/7580 at max intrinsic 10.00, so 5.4 would be an impossible
# price the stale gate rightly refuses; 9.4 clears floor x 1.15)
PLACED.clear()
jp.open_multileg_tradier({"instrument": "SPX", "direction": "short", "entry": 9.4,
                          "spread": {"type": "debit", "legs": [
                              {"action": "sell", "strike": 7580.0, "right": "call"},
                              {"action": "buy", "strike": 7570.0, "right": "call"}]},
                          "confidence": 60, "horizon": "day", "rationale": "x"},
                         "Sell SPX 7580C n Buy 7570C for 9.4 for today - Debit Spread",
                         "926", {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0})
check("debit: books LONG", PLACED and PLACED[0].direction == "long",
      str(PLACED[0].direction if PLACED else None))

# 5) empty brain instrument + text ticker -> SPXW (msg 3793 400 fix)
PLACED.clear()
jp.open_multileg_tradier({"instrument": "", "direction": "short", "entry": 4.7,
                          "spread": {"type": "credit", "legs": [
                              {"action": "sell", "strike": 7590.0, "right": "put"},
                              {"action": "buy", "strike": 7580.0, "right": "put"}]},
                          "confidence": 60, "horizon": "day", "rationale": "x"},
                         "Sell SPX 7590P n Buy 7580P n collect $470 for today",
                         "927", {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0})
check("empty instrument: text ticker -> SPXW",
      PLACED and PLACED[0].underlying == "SPXW",
      str(PLACED[0].underlying if PLACED else None))

# ---- canon instrument in the exit path (3783: 'SPX' must hit 'SPXW') ----
_cD = []
jp.close_trade = lambda t, px, why, st: _cD.append(t["id"])
jp.close_option = lambda t, px, q, why, st: _cD.append("O:" + t["id"])
stE = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
stE["open"]["SW1"] = {"id": "SW1", "instrument": "SPXW", "symbol": "SPXW",
                      "executor": "tradier", "status": "open_tradier",
                      "direction": "short", "qty": 1, "entry": 4.4,
                      "expiry": FUT,
                      "legs": [[7600.0, "put", "sell"], [7590.0, "put", "buy"]]}
jp.handle_exit_update("exit", {"instrument": "SPX", "direction": "flat"},
                      "3783", stE,
                      "SPX 7572 again, pl book gains on any trades below or close to 7570")
check("canon: 'SPX' exit hits the SPXW position", _cD == ["SW1"], str(_cD))

# ---- pre-place validation: unresolved symbol parks, no broker call ----
PLACED.clear()
jp.open_multileg_tradier({"instrument": "??", "direction": "short", "entry": 0.5,
                          "spread": {"type": "credit", "legs": [
                              {"action": "sell", "strike": 62.0, "right": "call"},
                              {"action": "buy", "strike": 60.0, "right": "call"}]},
                          "confidence": 50, "horizon": "day", "rationale": "x"},
                         "Sell 62C n Buy 60C n collect $50 for today", "928",
                         {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0})
check("prevalidate: unresolved symbol parked, not sent", len(PLACED) == 0)

# ---- cancel-stuck escalation at 3 tries ----
FAKE.update(cancel_ok=False, status="open", fill=None)
stF2 = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
tS = {"id": "S1", "executor": "tradier", "status": "close_pending",
      "close_order_id": "X1", "instrument": "BLK", "symbol": "BLK",
      "direction": "short", "cancel_close": True, "cancel_tries": 2, "qty": 1}
stF2["open"]["S1"] = tS
jp.manage_once(stF2)
check("cancel-stuck: try counter reaches 3 (push fired)", tS.get("cancel_tries") == 3,
      str(tS.get("cancel_tries")))
FAKE.update(cancel_ok=True)

# ---- entry-time ITM guard (TL3732 class): ITM short strike parks ----
def _bb_quote2(px):
    # sets the DEFAULT spot: the symbol table keeps per-ticker values, so a
    # later block on another ticker is not poisoned by this one
    jp.bbc = lambda: type("B", (), {
        "get_quote": staticmethod(lambda inst: float(px))})()
    _SPOTS["_default"] = float(px)
    for _k in list(_SPOTS):
        _SPOTS[_k] = float(px)          # a block-level quote overrides the table
NORMITM = {"instrument": "SPX", "direction": "short", "entry": 4.4, "qty": 1,
           "spread": {"type": "credit", "legs": [
               {"action": "sell", "strike": 7640.0, "right": "put"},
               {"action": "buy", "strike": 7630.0, "right": "put"}]},
           "confidence": 60, "horizon": "day", "rationale": "x"}
_bb_quote2(7545.0)
PLACED.clear()
jp.open_multileg_tradier(dict(NORMITM),
                         f"Sell SPX 7640P n Buy 7630P n collect $440 for {FUT_TXT}",
                         "930", {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0})
check("itm: short put 95 ITM -> parked, no order", len(PLACED) == 0)
_bb_quote2(7700.0)
PLACED.clear()
jp.open_multileg_tradier(dict(NORMITM),
                         f"Sell SPX 7640P n Buy 7630P n collect $440 for {FUT_TXT}",
                         "931", {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0})
check("itm: same strikes OTM at 7700 -> placed", len(PLACED) == 1)
_bb_quote2(7641.0)          # 1pt ITM: inside the 0.05% buffer (~3.8pt)
PLACED.clear()
jp.open_multileg_tradier(dict(NORMITM),
                         f"Sell SPX 7640P n Buy 7630P n collect $440 for {FUT_TXT}",
                         "932", {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0})
check("itm: 1pt graze inside buffer -> placed", len(PLACED) == 1)
# debit spread unaffected even with ITM-ish strikes (D147: entry clears the
# spot floor — spot 7600 makes 7590/7600 worth its 10.00 intrinsic)
_bb_quote2(7600.0)
PLACED.clear()
jp.open_multileg_tradier({"instrument": "SPX", "direction": "short", "entry": 9.4,
                          "spread": {"type": "debit", "legs": [
                              {"action": "sell", "strike": 7600.0, "right": "call"},
                              {"action": "buy", "strike": 7590.0, "right": "call"}]},
                          "confidence": 60, "horizon": "day", "rationale": "x"},
                         "Sell SPX 7600C n Buy 7590C for 9.4 - Debit Spread",
                         "933", {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0})
check("itm: debit spread not gated", len(PLACED) == 1)

# ---- 3584 boundary: 7720C short vs desk
_bb_quote2(7710.0)
PLACED.clear()
jp.open_multileg_tradier({"instrument": "SPX", "direction": "short", "entry": 5.35,
                          "spread": {"type": "credit", "legs": [
                              {"action": "sell", "strike": 7720.0, "right": "call"},
                              {"action": "buy", "strike": 7730.0, "right": "call"}]},
                          "confidence": 60, "horizon": "day", "rationale": "x"},
                         f"Sell SPX 7720C n Buy 7730C n collect $530 to $540 for {FUT_TXT}",
                         "934", {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0})
check("3584: 10pt OTM at 7710 -> placed", len(PLACED) == 1)
_bb_quote2(7726.0)
PLACED.clear()
jp.open_multileg_tradier({"instrument": "SPX", "direction": "short", "entry": 5.35,
                          "spread": {"type": "credit", "legs": [
                              {"action": "sell", "strike": 7720.0, "right": "call"},
                              {"action": "buy", "strike": 7730.0, "right": "call"}]},
                          "confidence": 60, "horizon": "day", "rationale": "x"},
                         f"Sell SPX 7720C n Buy 7730C n collect $530 to $540 for {FUT_TXT}",
                         "935", {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0})
check("3584: 6pt ITM at 7726 -> sent at 1 lot (D44 shrink-not-park)",
      len(PLACED) == 1 and PLACED[0].qty == 1, f"placed={len(PLACED)}")

# ---- 3587/3588: long-option sign + text % marks ----
PLACED.clear()
stO = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
jp.open_option_trade({"direction": "short", "action": "entry", "entry": 3.45,
                      "stop": None, "targets": [], "confidence": 60,
                      "horizon": "day", "rationale": "x",
                      "option": {"underlying": "GOOG", "strike": 285.0,
                                 "right": "put", "expiry": FUT}},
                     "3587", stO, f"GOOG 285P at 3.45 for {FUT_TXT} - High Risk, take small")
recO = next(iter(stO["open"].values()))
check("3587: long put books direction LONG despite brain 'short'",
      recO.get("direction") == "long", str(recO.get("direction")))
check("3587: lotto forced qty 1", recO.get("qty") == 1, str(recO.get("qty")))
okM2, _, normM2 = jp.validate_card(
    {"instrument": "GOOG", "direction": "short", "action": "none"}, "20% up")
check("3588: text '20% up' -> pct mark 20", normM2.get("pct_actual") == 20,
      str(normM2.get("pct_actual")))

# ---- 3589/3590: 2-digit strike shorthand + 'tomorrow's' expiry scope ----
_cE = []
jp.close_trade = lambda t, px, why, st: _cE.append(t["id"])
jp.close_option = lambda t, px, q, why, st: _cE.append("O:" + t["id"])
stS = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
stS["open"]["A1"] = {"id": "A1", "instrument": "SPXW", "symbol": "SPXW",
                     "executor": "tradier", "status": "open_tradier",
                     "direction": "short", "qty": 6, "entry": 2.65,
                     "expiry": FUT,
                     "legs": [[7735.0, "call", "sell"], [7745.0, "call", "buy"]]}
jp.handle_exit_update("exit", {"instrument": "SPX500", "direction": "short"},
                      "3589", stS, "Taking gains on 35/45")
check("3589: '35/45' hits 7735/7745", _cE == ["A1"], str(_cE))

_cE.clear()
from datetime import timedelta as _TD
_d0 = jp.us_session_for_today()
_d1 = _d0 + _TD(days=1)
while _d1.weekday() >= 5:
    _d1 += _TD(days=1)
stT2 = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
for tid, exp in (("T_now", _d0.isoformat()), ("T_tom", _d1.isoformat())):
    stT2["open"][tid] = {"id": tid, "instrument": "SPXW", "symbol": "SPXW",
                         "executor": "tradier", "status": "open_tradier",
                         "direction": "short", "qty": 1, "entry": 1.0, "expiry": exp,
                         "legs": [[7700.0, "call", "sell"], [7710.0, "call", "buy"]]}
jp.handle_exit_update("exit", {"instrument": "SPX500", "direction": "short"},
                      "3590", stT2, "Taking gains on tomorrow's bear calls")
check("3590: 'tomorrow's' closes only the tomorrow expiry", _cE == ["T_tom"], str(_cE))

# ---- 3593: ticket-only entry (TO OPEN image, no strike in text) ----
_SPOTS["FTNT"] = 136.0
PLACED.clear()
jp.hermes_parse = lambda text, photo=None: {
    "instrument": "FTNT", "direction": "short", "action": "none", "entry": None,
    "stop": None, "targets": [], "confidence": 50, "horizon": "", "rationale": "x",
    "trim": None, "spread": None,
    "option": {"underlying": "FTNT", "strike": 136, "right": "put",
               "expiry": FUT}}
_orig_enrich = jp.extract_image_fields
jp.extract_image_fields = lambda p, cfg: {
    "message_on_image": "BUY TO OPEN 5 FTNT 136.000 P 10/30/26",
    "underlying": "FTNT", "qty": 5, "fill_price": 2.79, "credit": 2.79,
    "strikes": [136], "structure": "single", "expiry": FUT}
import tempfile, os
_tf = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False); _tf.write(b"x"); _tf.close()
stTk = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
jp._parse_trade("FTNT Puts making now", "3593", _tf.name, stTk, 0)
check("3593: ticket-only TO OPEN forces an entry order", len(stTk["open"]) == 1
      and len(PLACED) == 1, f"open={len(stTk['open'])} placed={len(PLACED)}")
recTk = next(iter(stTk["open"].values())) if stTk["open"] else {}
check("3593: ticket qty capped by the $300 risk rule", recTk.get("qty") == 1, str(recTk.get("qty")))
os.unlink(_tf.name)
jp.extract_image_fields = _orig_enrich

# ---- 3595: 'all SPX Bear calls if any free runners' + stray level 7710 ----
_cF = []
jp.close_trade = lambda t, px, why, st: _cF.append(t["id"])
stR = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
for tid, exp, r in (("C1", "2026-11-04", "call"), ("C2", "2026-11-05", "call"),
                    ("P1", "2026-11-04", "put")):
    stR["open"][tid] = {"id": tid, "instrument": "SPXW", "symbol": "SPXW",
                        "executor": "tradier", "status": "open_tradier",
                        "direction": "short", "qty": 1, "entry": 1.0, "expiry": exp,
                        "legs": [[7700.0, r, "sell"], [7710.0, r, "buy"]]}
jp.handle_exit_update("exit", {"instrument": "SPX500", "direction": "short"},
                      "3595", stR,
                      "Take gains on all SPX Bear calls if any free runners, SPX Back to 7710")
check("3595: all call spreads closed, puts and level-scope untouched",
      set(_cF) == {"C1", "C2"} and "P1" in stR["open"], str(_cF))

# ---- 3600: ticker rescue works on plain "Puts" too ----
check("3600: plain 'FTNT Puts' still yields the ticker",
      jp.scope_instrument("50% up on FTNT Puts, Pl take gains") == "FTNT")

# ---- 3600 residuals: plain 'Puts' sets family; close keeps the mark ----
check("3600: plain 'Puts' resolves family P", jp.scope_family("50% up on FTNT Puts, Pl take gains") == "P")
B10 = jp.Book()
pk = B10.open("k1", "x", structure="single_option", underlying="FTNT", body=136.0, qty=5)
jp._load_book = lambda: B10
jp._save_book = lambda b: None
jp.book_apply({"instrument": "FTNT", "direction": "short", "action": "exit", "pct_actual": 50},
              "50% up on FTNT Puts, Pl take gains", "3600", {})
check("3600: close kept the 50% mark", pk.marks and pk.marks[-1].get("pct") == 50,
      str(pk.marks))
check("3600: position closed", not pk.is_open)

# ---- 3602 multi-ticker exit ----
_cG = []
jp.close_trade = lambda t, px, why, st: _cG.append(t["id"])
stM = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
_dm = jp.us_session_for_today() + _TD(days=1)
while _dm.weekday() >= 5:
    _dm += _TD(days=1)
for tid, inst_sym, r in (("Q1", "QQQ", "call"), ("S1", "SPXW", "call"), ("P2", "QQQ", "put")):
    stM["open"][tid] = {"id": tid, "instrument": inst_sym, "symbol": inst_sym,
                        "executor": "tradier", "status": "open_tradier",
                        "direction": "short", "qty": 1, "entry": 1.0,
                        "expiry": _dm.isoformat(),
                        "legs": [[710.0 if inst_sym == "QQQ" else 7550.0, r, "sell"],
                                 [712.0 if inst_sym == "QQQ" else 7560.0, r, "buy"]]}
jp.handle_exit_update("exit", {"instrument": "QQQ", "direction": "short"}, "3602", stM,
                      "Book gains on QQQ Bear calls and SPX bear calls of tomorrow")
check("3602: BOTH tickers' call spreads close, put untouched",
      set(_cG) == {"Q1", "S1"} and "P2" in stM["open"], str(_cG))

# ---- 3603 'we can ignore' ----
jp.hermes_parse = lambda text, photo=None: {
    "instrument": "SPX500", "direction": "short", "action": "exit", "entry": None,
    "stop": None, "targets": [], "confidence": 50, "horizon": "", "rationale": "x",
    "trim": None, "spread": None, "option": None}
_cG.clear()
stI = {"seen_msg": {}, "open": {"S1": {"id": "S1", "instrument": "SPXW",
                                       "symbol": "SPXW", "executor": "tradier",
                                       "status": "open_tradier", "direction": "short",
                                       "qty": 1, "entry": 1.0, "expiry": "2026-11-05",
                                       "legs": [[7550.0, "call", "sell"], [7560.0, "call", "buy"]]}},
       "closed": {}, "alerts_today": 0}
jp._parse_trade("Take gains on Kalshi Bets on SPX which we did yesterday -- we can ignore as we do not trade on kalshi",
                "3603", None, stI, 0)
check("3603: ignore -> nothing closed", _cG == [], str(_cG))

# ---- 3604 naked short put: gate allows, books short, qty from '2C' ----
from james_core import Record, MsgClass, Action, Leg, RiskPolicy
recN = Record(msg_id="3604", ts="", cls=MsgClass.NAKED, action=Action.ENTRY,
              raw="", direction="short",
              legs=[Leg(strike=711.0, right="put", action="sell")])
okN, whyN = RiskPolicy(phase=2).allow(recN)
check("3604: naked short put allowed", okN, whyN)
PLACED.clear()
_SPOTS["QQQ"] = 700.0
stN = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
jp.open_option_trade({"direction": "short", "action": "entry", "entry": 1.16,
                      "stop": None, "targets": [], "confidence": 60,
                      "horizon": "day", "rationale": "x",
                      "option": {"underlying": "QQQ", "strike": 711.0,
                                 "right": "put", "expiry": FUT}},
                     "3604", stN, "Sell QQQ 711P n collect $116 for today - 2C")
recN2 = next(iter(stN["open"].values()))
check("3604: books SHORT", recN2.get("direction") == "short", str(recN2.get("direction")))
check("3604: qty from '2C' inside the risk cap", recN2.get("qty") in (1, 2), str(recN2.get("qty")))

# ---- 3605: premium scale, naked observational tag, circuit breaker ----
check("scale: 'collect $76' -> 0.76", jp.text_credit("collect $76") == 0.76)
FAKE.update(status="filled", fill=0.76)
sB = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
for i, pnl in enumerate((-100.0, -120.0, -90.0)):
    sB["closed"][f"L{i}"] = {"id": f"L{i}", "naked": True, "pnl": pnl,
                             "exit_ts": jp.today() + " 01:0%d:00" % i}
PLACED.clear()
jp.open_option_trade({"direction": "short", "action": "entry", "entry": 0.76,
                      "stop": None, "targets": [], "confidence": 60, "horizon": "day",
                      "rationale": "x",
                      "option": {"underlying": "SPY", "strike": 766.0, "right": "put",
                                 "expiry": FUT}},
                     "3605b", sB, "Sell SPY 766P n collect $76 for today - 2C")
check("breaker: 3 naked losses today -> no order", len(PLACED) == 0)
_SPOTS["SPY"] = 766.0
sB2 = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
PLACED.clear()
jp.open_option_trade({"direction": "short", "action": "entry", "entry": 0.76,
                      "stop": None, "targets": [], "confidence": 60, "horizon": "day",
                      "rationale": "x",
                      "option": {"underlying": "SPY", "strike": 766.0, "right": "put",
                                 "expiry": FUT}},
                     "3605c", sB2, "Sell SPY 766P n collect $76 for today - 2C")
recB = next(iter(sB2["open"].values()))
check("naked: placed, tagged observational", recB.get("naked") is True and
      "NAKED" in (recB.get("fill_note") or ""), str(recB.get("fill_note")))
check("naked: premium 0.76 not 76", recB.get("entry") == 0.76, str(recB.get("entry")))

# ---- 3606: partial structure exit closes only the call legs ----
_cP = []
class _PT:
    def close(self, trade, limit_price=None):
        _cP.append(list(trade["legs"]))
        class R: ok = True; order_id = "PC1"; venue = "tradier"; reason = "stub"; fill_price = None
        return R()
jp._TRADIER_INST = _PT()
stFl = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
stFl["open"]["FLY"] = {"id": "FLY", "instrument": "AMZN", "symbol": "AMZN",
                       "executor": "tradier", "status": "open_tradier",
                       "direction": "short", "qty": 5, "entry": 6.0,
                       "expiry": FUT,
                       "legs": [[255.0, "call", "sell"], [260.0, "call", "buy"],
                                [250.0, "put", "buy"], [255.0, "put", "sell"]]}
jp.handle_exit_update("exit", {"instrument": "AMZN", "direction": "short"}, "3606", stFl,
                      "Take gains on AMZN 255/260 Bear call Leg of the Iron fly, we can hold 250/255 Bull Put for earnings")
fly = stFl["open"]["FLY"]
check("3606: call legs closed via one order",
      _cP and {l[1] for l in _cP[0]} == {"call"}, str(_cP))
check("3606: put legs remain", {l[1] for l in fly["legs"]} == {"put"} and
      len(fly["legs"]) == 2, str(fly["legs"]))
check("3606: position still open", "FLY" in stFl["open"])

# ---- guard scope: high-quality long-dated ITM passes, short/low parks ----
jp._TRADIER_INST = _FakeTradier()
_bb_quote2(374.0)
PLACED.clear()
jp.open_multileg_tradier({"instrument": "GD", "direction": "short", "entry": 6.98,
                          "spread": {"type": "credit", "legs": [
                              {"action": "sell", "strike": 390.0, "right": "put"},
                              {"action": "buy", "strike": 380.0, "right": "put"}]},
                          "confidence": 60, "horizon": "swing", "rationale": "x"},
                         f"GD Sell 390P n Buy 380P n collect $698 for "
                         f"{(_dt.date.today() + _dt.timedelta(days=16)).strftime('%m/%d')} "
                         f"- Bullish for GD earnings",
                         "941", {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0})
# D40-family fix (2026-11-18): the fixture hardcoded 11/20, which aged into
# the guard's dte<=2 window on 11-18 (16d -> 2d) and started parking a trade
# designed to PLACE. Rolling expiry keeps it 16 days out forever: the point of
# this fixture is "high quality + far dated ITM -> the guard allows it".
check("guard: 69.8% quality, 16d ITM -> placed", len(PLACED) == 1)
PLACED.clear()
jp.open_multileg_tradier({"instrument": "SPX", "direction": "short", "entry": 4.4,
                          "spread": {"type": "credit", "legs": [
                              {"action": "sell", "strike": 7640.0, "right": "put"},
                              {"action": "buy", "strike": 7630.0, "right": "put"}]},
                          "confidence": 60, "horizon": "day", "rationale": "x"},
                         "Sell SPX 7640P n Buy 7630P n collect $440 for tomorrow",
                         "942", {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0})
check("guard: 40% quality next-day ITM -> parked", len(PLACED) == 0)

# ---- 3883/3885: a ticket with BOTH 'TO OPEN' sides is a spread fill, not a single ----
import tempfile as _tf_mod
jp.hermes_parse = lambda text, photo=None: {
    "instrument": "SPX", "direction": "short", "action": "entry", "entry": None,
    "stop": None, "targets": [], "confidence": 50, "horizon": "", "rationale": "x",
    "trim": None, "spread": None,
    "option": {"underlying": "SPXW", "strike": 7700.0, "right": "call",
               "expiry": FUT}}
_oei = jp.extract_image_fields
jp.extract_image_fields = lambda p, cfg: {
    "message_on_image": "SELL TO OPEN 5 SPXW 7700C | BUY TO OPEN 5 SPXW 7715C",
    "underlying": "SPXW", "qty": 10, "fill_price": 17.0, "strikes": [7700],
    "structure": "single", "expiry": FUT}
PLACED.clear()
_SPOTS["SPXW"] = _SPOTS["SPX"] = 7700.0
_t1 = _tf_mod.NamedTemporaryFile(suffix=".jpg", delete=False); _t1.write(b"x"); _t1.close()
_stTK = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
jp._parse_trade("", "X9", _t1.name, _stTK, 0)
check("multi-TO-OPEN ticket: places the SPREAD (short), qty capped 1 (D72)",
      PLACED and PLACED[0].direction == "short" and PLACED[0].qty == 1,
      str([(r.direction, r.qty) for r in PLACED]))
os.unlink(_t1.name)
jp.extract_image_fields = _oei

# ---- 3903: 'Read this as' parses the inner command (debit call, direction long) ----
jp.hermes_parse = lambda text, photo=None: {
    "instrument": "SPX", "direction": "short", "action": "none", "entry": None,
    "stop": None, "targets": [], "confidence": 50, "horizon": "", "rationale": "x",
    "trim": None, "spread": None, "option": None}
PLACED.clear()
_SPOTS["SPXW"] = _SPOTS["SPX"] = 7710.0
stCl = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
jp._parse_trade("Read this as Sell 7705 n buy 7715 for today", "3903", None, stCl, 0)
check("3903: clarification parsed into an entry", len(PLACED) == 1, str(len(PLACED)))
recCl = PLACED[0] if PLACED else None
check("3903: bear-call credit legs, books SHORT",
      recCl and recCl.direction == "short" and
      {(l.strike, l.right, l.action) for l in recCl.legs} ==
      {(7705.0, "call", "sell"), (7715.0, "call", "buy")},
      str([(l.strike, l.right, l.action) for l in recCl.legs] if recCl else []))

# ---- manual settle at the close print ----
_bb_quote2(7649.5)
stMS = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0,
        "manual_settle": {"PH1": {"opt_id": "SPXW 261104C07700000",
                                  "entry": 17.0, "qty": 10, "expiry": "2026-11-04"}}}
stMS["closed"]["PH1"] = {"id": "PH1", "pnl": None, "exit_ts": "2026-11-04 EOD"}
jp.manage_manual_settle(stMS, after_hour=0)
check("settle: phantom call priced worthless (-17,000)",
      stMS["closed"]["PH1"]["pnl"] == -17000.0 and not stMS["manual_settle"],
      str(stMS["closed"]["PH1"]["pnl"]))

# ---- James outcome capture on his book close (+ our link) ----
import tempfile as _tfm2
_jout = _tfm2.NamedTemporaryFile(suffix=".csv", delete=False); _jout.close()
jp.JOUT = jp.Path(_jout.name)
_oB = jp.Book()
_oP = _oB.open("J1", "x", structure="bear_call_spread", underlying="SPXW",
               body=7700.0, wing_high=7710.0, qty=5)
jp._load_book = lambda: _oB
jp._save_book = lambda b: None
_oSt = {"seen_msg": {}, "open": {"M1": {"id": "M1", "instrument": "SPXW",
        "symbol": "SPXW", "status": "open_tradier", "direction": "short",
        "qty": 1, "entry": 2.0}}, "closed": {}, "alerts_today": 0}
_called=[]
_orig_jor=jp.james_outcome_row
jp.james_outcome_row=lambda *a,**k: (_called.append(a[0]), _orig_jor(*a,**k))[1]
jp.book_apply({"instrument": "SPX500", "direction": "short", "action": "exit",
               "pct_actual": 200}, "200% up on SPX bear calls, Pl book gains",
              "Y1", _oSt)
jp.james_outcome_row=_orig_jor
check("james outcome: writer called", bool(_called), str(_called))
import csv as _csv2
_rows = list(_csv2.DictReader(open(_jout.name, encoding="utf-8")))
check("james outcome: row written with win + our_trade link",
      _rows and _rows[0]["result"] == "win" and _rows[0]["our_trade"] == "M1",
      str(_rows))
os.unlink(_jout.name)

# ---- 3613 all/both trades + 3614 green-when arm + 3614 multi-date ----
_cH = []
jp.close_trade = lambda t, px, why, st: _cH.append(t["id"])
stAll = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
for tid, exp in (("X1", FUT), ("X2", FUT)):
    stAll["open"][tid] = {"id": tid, "instrument": "SPXW", "symbol": "SPXW",
                          "executor": "tradier", "status": "open_tradier",
                          "direction": "short", "qty": 1, "entry": 1.0, "expiry": exp,
                          "legs": [[7600.0, "put", "sell"], [7590.0, "put", "buy"]]}
jp.handle_exit_update("exit", {"instrument": None, "direction": "short"}, "3613", stAll,
                      "Took gains in all trades of funded accounts")
check("3613: 'all trades' closes everything", set(_cH) == {"X1", "X2"}, str(_cH))

# green-when: red position arms (not closed)
_cH.clear()
_bb_quote2(7620.0)
stG2 = {"seen_msg": {}, "open": {"G1": {"id": "G1", "instrument": "SPXW",
        "symbol": "SPXW", "executor": "tradier", "status": "open_tradier",
        "direction": "short", "qty": 1, "entry": 7.55,
        "expiry": jp.us_session_for_today().isoformat(),  # text: 'today and tomorrow'
        "legs": [[7700.0, "put", "sell"], [7685.0, "put", "buy"]]}},
        "closed": {}, "alerts_today": 0}
jp.handle_exit_update("exit", {"instrument": "SPX500", "direction": "short"}, "3614", stG2,
                      "Be ready to book gains on SPX Bull Puts of today and tomorrow when you see them in Green")
check("3614: red bull put ARMED not closed",
      not _cH and stG2["open"]["G1"].get("green_when", {}).get("level") == 7703.85,
      f"{_cH} / {stG2['open']['G1'].get('green_when')}")

# manage: green trigger closes
_cH.clear()
_bb_quote2(7720.0)
jp.manage_once(stG2)
check("3614: green level reached -> closes", _cH == ["G1"], str(_cH))

# multi-date 'of today and tomorrow'
_cH.clear()
from datetime import timedelta as _TDx
_da = jp.us_session_for_today()
_db = _da + _TDx(days=1)
while _db.weekday() >= 5:
    _db += _TDx(days=1)
stMD = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
for tid, exp in (("D1", _da.isoformat()), ("D2", _db.isoformat()), ("D3", "2026-12-01")):
    stMD["open"][tid] = {"id": tid, "instrument": "SPXW", "symbol": "SPXW",
                         "executor": "tradier", "status": "open_tradier",
                         "direction": "short", "qty": 1, "entry": 1.0, "expiry": exp,
                         "legs": [[7600.0, "put", "sell"], [7590.0, "put", "buy"]]}
jp.handle_exit_update("exit", {"instrument": "SPX500", "direction": "short"}, "MD", stMD,
                      "book gains on SPX Bull Puts of today and tomorrow")
check("multi-date: today AND tomorrow close, later date stays",
      set(_cH) == {"D1", "D2"} and "D3" in stMD["open"], str(_cH))

# ---- quote adapter: routing, proxy fallback, cache, mark order ----
import james_pipe as _jpq
_envs = []
def _fake_fetch(env, occ):
    _envs.append((env, occ))
    if occ.startswith("SPY"):
        return 0.20 if env == "prod" else None
    if env == "sandbox":
        return None if occ.startswith("SPXW") else None
    return 1.30 if not occ.startswith("SPY") else 0.20
_jpq._fetch_quote = _fake_fetch
_jpq._Q_CACHE.clear()
_envs.clear()
val = _jpq.opt_quote("SPXW261107C07700000")
check("quote: the SPXW contract itself only on SANDBOX",
      _envs and _envs[0] == ("sandbox", "SPXW261107C07700000") and
      all(o.startswith("SPY") == (e == "prod") for e, o in _envs), str(_envs))
check("quote: proxy fallback scales 0.20 x10 = 2.0", val == 2.0, str(val))
_envs.clear()
v2 = _jpq.opt_quote("BMRN261120C00060000")
check("quote: equity tries PROD first", _envs and _envs[0][0] == "prod", str(_envs))
check("quote: equity returns prod value", v2 == 1.30, str(v2))
_n0 = len(_envs)
_jpq.opt_quote("BMRN261120C00060000")
check("quote: cached within ttl", len(_envs) == _n0, str(len(_envs)))
_jpq.opt_mark  # mark uses adapter first
_orig_oq = _jpq.opt_quote
_jpq.opt_quote = lambda *a, **k: 1.55
_m = _REAL_OPT_MARK({"opt_id": "BMRN261120C00060000"})
check("mark: adapter value returned (no Tiger needed)", _m == 1.55, str(_m))
_jpq.opt_quote = _orig_oq

# ---- 3616 zones + 3619-side partial (fly put side via '7700/7710 Bull Puts') ----
check("3616: above/below zones captured",
      (7700.0, "below") in jp.text_levels("Bull Puts below 7700 and Bear calls above 7710 are good to enter"))
_cZ = []
class _ZT:
    def close(self, trade, limit_price=None):
        _cZ.append(list(trade["legs"]))
        class R: ok = True; order_id = "ZC1"; venue = "tradier"; reason = "stub"; fill_price = None
        return R()
jp._TRADIER_INST = _ZT()
stFl2 = {"seen_msg": {}, "open": {"FLY2": {"id": "FLY2", "instrument": "SPXW",
        "symbol": "SPXW", "executor": "tradier", "status": "open_tradier",
        "direction": "short", "qty": 4, "entry": 6.55, "expiry": FUT,
        "legs": [[7690.0, "put", "buy"], [7700.0, "put", "sell"],
                 [7700.0, "call", "sell"], [7710.0, "call", "buy"]]}},
        "closed": {}, "alerts_today": 0}
jp.handle_exit_update("exit", {"instrument": "SPX500", "direction": "short"}, "3619", stFl2,
                      "SPX close to 7708, Pl book gains on 7700/7710 Bull Puts, it will go to 7710 and then reverse")
fly2 = stFl2["open"]["FLY2"]
check("3619: PUT side closed as a whole (sibling 7690 included)",
      _cZ and {l[1] for l in _cZ[0]} == {"put"} and len(_cZ[0]) == 2, str(_cZ))
check("3619: call side remains", {l[1] for l in fly2["legs"]} == {"call"})

# ---- painted exit message (3618-style chart words) closes the puts ----
_cQ2 = []
jp.close_trade = lambda t, px, why, st: _cQ2.append(t["id"])
jp.hermes_parse = lambda text, photo=None: {
    "instrument": "SPX500", "direction": "short", "action": "none", "entry": None,
    "stop": None, "targets": [], "confidence": 50, "horizon": "", "rationale": "x",
    "trim": None, "spread": None, "option": None}
_oei2 = jp.extract_image_fields
jp.extract_image_fields = lambda p, cfg: {
    "message_on_image": "Pl book gains on all SPX Bull Puts",
    "chart_levels": [7711.34, 7691.58]}
_pt3 = _tfm2.NamedTemporaryFile(suffix=".jpg", delete=False); _pt3.write(b"x"); _pt3.close()
stPt = {"seen_msg": {}, "open": {"PB1": {"id": "PB1", "instrument": "SPXW",
        "symbol": "SPXW", "executor": "tradier", "status": "open_tradier",
        "direction": "short", "qty": 2, "entry": 3.0, "expiry": FUT,
        "legs": [[7700.0, "put", "sell"], [7690.0, "put", "buy"]]},
        "PC1": {"id": "PC1", "instrument": "SPXW", "symbol": "SPXW",
        "executor": "tradier", "status": "open_tradier", "direction": "short",
        "qty": 1, "entry": 1.0, "expiry": FUT,
        "legs": [[7710.0, "call", "sell"], [7720.0, "call", "buy"]]}},
        "closed": {}, "alerts_today": 0}
jp._parse_trade("", "3618p", _pt3.name, stPt, 0)
check("painted: exit routed from image words (puts closed, call kept)",
      _cQ2 == ["PB1"] and "PC1" in stPt["open"], str(_cQ2))
os.unlink(_pt3.name)
jp.extract_image_fields = _oei2

# ---- 'Small Size' caps qty ----
jp._TRADIER_INST = _FakeTradier()
_bb_quote2(7690.0)
PLACED.clear()
jp.open_multileg_tradier({"instrument": "SPX", "direction": "short", "entry": 3.7,
                          "qty": 6,
                          "spread": {"type": "credit", "legs": [
                              {"action": "sell", "strike": 7710.0, "right": "call"},
                              {"action": "buy", "strike": 7720.0, "right": "call"}]},
                          "confidence": 60, "horizon": "day", "rationale": "x"},
                         "Sell SPX 7710C n Buy 7720C n collect $370 to $380 - Small Size",
                         "3620", {"seen_msg": {}, "open": {}, "closed": {},
                                  "alerts_today": 0})
check("small size: qty capped at 1 (D72)", PLACED and PLACED[0].qty == 1,
      str(PLACED[0].qty if PLACED else None))

# ---- 3623-3626: bare mark %, tickerless-entry inference, trim-vs-'% on' ----
okBM, _, nBM = jp.validate_card({"direction": "short", "action": "none"}, "40%")
check("bare % message stores a mark", nBM.get("pct_actual") == 40, str(nBM.get("pct_actual")))
okTI, _, nTI = jp.validate_card({"direction": "short", "action": "entry"},
                                "Sell 7705C n Buy 7715C n collect $450 to $460 for today")
check("3913: 4-digit strikes infer the instrument (no INVALID)",
      okTI and nTI.get("instrument") == "SPX", nTI.get("instrument") if nTI else None)
check("3626: trim word beats '% on' -> 0.8",
      jp.parse_exit_directive({}, "80% on 7710/7720 Bear call,s Trim 80%")[2] == 0.8)
check("contrast: '% on' alone stays a full close",
      jp.parse_exit_directive({}, "60% on QQQ Bear calls, pl book gains")[2] == 1.0)

# ---- 3626 trim: fraction survives the engine, order qty, partial booking ----
class _CT:
    def __init__(self): self.calls = []
    def close(self, trade, limit_price=None, qty=None):
        self.calls.append((trade.get("id"), qty))
        class R: ok = True; order_id = "TC1"; venue = "tradier"; reason = "stub"; fill_price = None
        return R()
_ct = _CT()
jp._TRADIER_INST = _ct
EAf, tagf, matchedf, whyf = jp.resolve_exit(
    {"instrument": "SPX500", "direction": "short"},
    "80% on 7710/7720 Bear call,s Trim 80%", "3626",
    {"A": {"id": "A", "instrument": "SPXW", "symbol": "SPXW", "executor": "tradier",
           "status": "open_tradier", "direction": "short", "qty": 5, "entry": 5.0,
           "expiry": FUT,
           "legs": [[7710.0, "call", "sell"], [7720.0, "call", "buy"]]}})
check("3626: engine keeps fraction 0.8", EAf is not None and EAf.close_fraction == 0.8,
      str(EAf.close_fraction if EAf else None))
tT = {"id": "T9", "executor": "tradier", "status": "open_tradier", "qty": 5,
      "instrument": "SPXW", "symbol": "SPXW", "direction": "short", "entry": 5.0,
      "expiry": FUT, "legs": [[7710.0, "call", "sell"], [7720.0, "call", "buy"]]}
_stubCT = jp.close_trade
jp.close_trade = _REAL_CLOSE
jp.execute_exit_action(EAf, [tT], {"instrument": "SPX"}, "3626",
                       {"seen_msg": {}, "open": {"T9": tT}, "closed": {}, "alerts_today": 0})
jp.close_trade = _stubCT
check("3626: close order carries 4 of 5 contracts", _ct.calls == [("T9", 4)], str(_ct.calls))
_b9 = jp.Book(); jp._load_book = lambda: _b9; jp._save_book = lambda b: None
stBk = {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0}
tB = {"id": "B9", "instrument": "SPXW", "direction": "short", "qty": 5, "entry": 5.0,
      "close_qty": 4, "close_reason": "trim"}
jp.book_tradier_close(tB, 3.0, stBk)
check("3626: trim books on 4, 1 remains open",
      tB.get("pnl") == 800.0 and tB.get("qty") == 1 and tB.get("status") == "open_tradier",
      f"{tB.get('pnl')}/{tB.get('qty')}/{tB.get('status')}")

# ---- reason misattribution: update branch uses the guard too ----
check("reason guard: mismatch falls back to raw",
      jp.reason_text({"rationale": "5.5k to go"}, "SPX below 7660, book now") == "SPX below 7660, book now")

# ---- 3634 tense nuance: 'taking gains ... will wait to enter' is an EXIT ----
T3634 = "Taking gains on bear calls, will wait for pull back to enter"
check("3634: exit command survives a future re-entry clause",
      jp.non_entry_class(T3634) == "exit" and jp.exit_command_wins(T3634))
check("contrast: 'will book gains later' stays a prediction",
      jp.non_entry_class("will book gains later today") == "prediction")
_cT2 = []
jp.close_trade = lambda t, px, why, st: _cT2.append(t["id"])
st34 = {"seen_msg": {}, "open": {"K1": {"id": "K1", "instrument": "SPXW",
        "symbol": "SPXW", "executor": "tradier", "status": "open_tradier",
        "direction": "short", "qty": 1, "entry": 1.0, "expiry": FUT,
        "legs": [[7710.0, "call", "sell"], [7720.0, "call", "buy"]]}},
        "closed": {}, "alerts_today": 0}
jp.handle_exit_update("exit", {"instrument": None, "direction": "short"}, "3634", st34, T3634)
check("3634: call spread closed by the exit", _cT2 == ["K1"], str(_cT2))

# ---- 3634: re-entry plan captured as a trigger ----
_trg = jp.text_trigger("Taking gains on bear calls, will wait for pull back to enter")
check("3634: wait-to-enter plan captured", _trg and "wait for pull back" in _trg, str(_trg))

# ---- 3640: range levels captured ----
check("3640: '7660 to 7670' captured as range",
      jp.text_levels("7660 to 7670 possible during Europe session") ==
      [(7660.0, "range"), (7670.0, "range")])

# ---- 3637: 'Put limit at $300' = sell limit floor ----
check("3637: floor parsed to 3.00", jp.credit_floor("collect $405 to $410 - Put limit at $300") == 3.0)
_bb_quote2(7700.0)
PLACED.clear()
jp._TRADIER_INST = _FakeTradier()
jp.open_multileg_tradier({"instrument": "SPX", "direction": "short", "entry": 4.05, "qty": 6,
                          "spread": {"type": "credit", "legs": [
                              {"action": "sell", "strike": 7635.0, "right": "put"},
                              {"action": "buy", "strike": 7625.0, "right": "put"}]},
                          "confidence": 60, "horizon": "day", "rationale": "x"},
                         "Sell 7635P n Buy 7625P and collect $405 to $410 - Put limit at $300",
                         "3637c", {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0})
check("3637: order limit = floor 3.00 (not 4.05)", PLACED and PLACED[0].credit == 3.0,
      str(PLACED[0].credit if PLACED else None))

# ---- 3955-class: ticket text with SELL/BUY/TO noise still resolves SPXW ----
_bb_quote2(7500.0)
PLACED.clear()
jp._TRADIER_INST = _FakeTradier()
jp.open_multileg_tradier({"instrument": "Bear Call Spread", "direction": "short",
                          "entry": 5.65,
                          "spread": {"type": "credit", "legs": [
                              {"action": "sell", "strike": 7630.0, "right": "call"},
                              {"action": "buy", "strike": 7640.0, "right": "call"}]},
                          "confidence": 60, "horizon": "day", "rationale": "x"},
                         "Bear Call Spread (Credit) SELL TO OPEN 25 SPXW 7630.000 C 11/06/26 BUY TO OPEN 25 SPXW 7640.000 C 11/06/26",
                         "3955c", {"seen_msg": {}, "open": {}, "closed": {},
                                   "alerts_today": 0})
check("3955: ticket noise -> SPXW, qty capped 1 (D72)",
      PLACED and PLACED[0].underlying == "SPXW" and PLACED[0].qty == 1,
      str((PLACED[0].underlying, PLACED[0].qty) if PLACED else None))

# ---- symbol repair layer: park only if repair is exhausted ----
check("repair: label -> known ticket symbol", jp.repair_symbol("Bear Call Spread", "SELL TO OPEN 25 SPXW 7630 C")[0] == "SPXW")
check("repair: noisy equity label -> ticker", jp.repair_symbol("aapl inc", "buy aapl 100C")[0] == "AAPL")
check("repair: class share separators stripped (BRK.B -> BRKB)",
      jp.repair_symbol("BRK.B Holdings", "")[0] == "BRKB" and
      jp.repair_symbol("BRK/B quotes", "")[0] == "BRKB")
check("repair: exhausted -> None", jp.repair_symbol("!!", "zzz")[0] is None)
_bb_quote2(7500.0)
PLACED.clear()
jp._TRADIER_INST = _FakeTradier()
jp.open_multileg_tradier({"instrument": "Bear Call Spread", "direction": "short",
                          "entry": 5.65,
                          "spread": {"type": "credit", "legs": [
                              {"action": "sell", "strike": 7630.0, "right": "call"},
                              {"action": "buy", "strike": 7640.0, "right": "call"}]},
                          "confidence": 60, "horizon": "day", "rationale": "x"},
                         "SELL TO OPEN 25 SPXW 7630.000 C | BUY TO OPEN 25 SPXW 7640.000 C",
                         "SYM1", {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0})
check("repair: garbage instrument still places SPXW", PLACED and PLACED[0].underlying == "SPXW",
      str(PLACED[0].underlying if PLACED else None))

# ---- strike grid + listing pre-check ----
check("grid: equity keeps half strikes", jp.opt_strike_valid("TSLA", 337.5) == 337.5)
check("grid: SPXW still snaps to 5", jp.opt_strike_valid("SPXW", 7637.0) == 7635)
_of = jp._fetch_quote
def _fake_chains(env, url_ok=True):
    pass
_orig_listed = jp._listed
jp._listed = lambda u, e, r, k: (False, True, None)
# spot pinned BEFORE the first call too: at EOG's default 145 the FAR guard
# (D02) parks 75% away and the check would pass for the wrong reason. Both
# calls are about listing, and 'collect $60' (0.60 on a 1-wide = 60% of width)
# clears the D18 plausibility guard — $100 = 100% of width is impossible and
# the guard correctly parked it before listing was ever consulted.
_SPOTS["EOG"] = 36.0
PLACED.clear()
jp._TRADIER_INST = _FakeTradier()
jp.open_multileg_tradier({"instrument": "EOG", "direction": "short", "entry": 1.0,
                          "spread": {"type": "credit", "legs": [
                              {"action": "sell", "strike": 36.0, "right": "put"},
                              {"action": "buy", "strike": 35.0, "right": "put"}]},
                          "confidence": 60, "horizon": "day", "rationale": "x"},
                         f"Sell EOG 36P n Buy 35P n collect $60 for {FUT_TXT}",
                         "999", {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0})
check("listing: unlisted -> parked, not sent", len(PLACED) == 0)
jp._listed = lambda u, e, r, k: (True, True, None)
PLACED.clear()
jp.open_multileg_tradier({"instrument": "EOG", "direction": "short", "entry": 1.0,
                          "spread": {"type": "credit", "legs": [
                              {"action": "sell", "strike": 36.0, "right": "put"},
                              {"action": "buy", "strike": 35.0, "right": "put"}]},
                          "confidence": 60, "horizon": "day", "rationale": "x"},
                         f"Sell EOG 36P n Buy 35P n collect $60 for {FUT_TXT}",
                         "998", {"seen_msg": {}, "open": {}, "closed": {}, "alerts_today": 0})
check("listing: listed -> placed", len(PLACED) == 1)
jp._listed = _orig_listed

# ---- final verdict: phase-2 failures must reach the exit code -----------
# (the mid-file SystemExit at phase 1 left this file exiting 0 with FAIL lines)
print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    raise SystemExit(1)
print("ALL PASS")
