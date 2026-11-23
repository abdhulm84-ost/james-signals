#!/usr/bin/env python3
"""signal_intent.py — ONE canonical source of truth for reading James's trade
intent from raw text (2026-11-19).

WHY THIS FILE EXISTS (the meta-fix behind D110/D111/D112):
The verb knowledge was duplicated in THREE places, and each bug fix only
patched ONE of them:
  * text_credit (D20)  — fixed the gerund 'collecting' for PREMIUM only
  * text_is_naked_short (D110) — fixed 'sold' for DIRECTION only
  * message_tag.classify (D112) — fixed 'sold' for ARRIVAL only
So 'Sold X collected $Y' got fixed in one spot and resurfaced in the other two.
This module is the single lexicon every consumer imports. No consumer may keep
its own verb list. A verb added here is fixed EVERYWHERE at once.

It also answers the two design rules the user set (2026-11-19):
  1. UNLESS A MESSAGE IS CLEAR, DO NOT CALL IT NARRATIVE. A message with a
     strike + ticker + premium but no clean verb is 'unclear', NOT 'narrative' —
     and 'unclear' still triggers the LSE flow check at arrival (narrative
     silently skips it). The tape is exactly the thing that resolves an
     unclear short-vs-long read.
  2. FINGERPRINT + PRIOR-SHAPE LOOKUP: when a message is unclear, find the
     same structural shape in the defect register / ledger / park outcomes and
     surface what happened last time — never re-learn the same lesson.
"""
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# the ONE verb lexicon. Edit here, fix everywhere.
# ---------------------------------------------------------------------------
SELL_WORDS = ("sell", "sold", "selling", "write", "wrote", "writing",
              "short", "shorting", "shorted")
BUY_WORDS = ("buy", "bought", "buying", "long", "grab", "grabbing",
             "enter", "entering", "pick up", "picking up", "picked up")
COLLECT_WORDS = ("collect", "collected", "collecting", "coleect")
PAY_WORDS = ("pay", "paid", "paying")

SELL_RE = re.compile(r"\b(?:" + "|".join(SELL_WORDS) + r")\b", re.I)
BUY_RE = re.compile(r"\b(?:" + "|".join(BUY_WORDS) + r")\b", re.I)
COLLECT_RE = re.compile(r"\b(?:" + "|".join(COLLECT_WORDS) + r")\b", re.I)
PAY_RE = re.compile(r"\b(?:" + "|".join(PAY_WORDS) + r")\b", re.I)

SPREAD_RE = re.compile(
    r"\b(spread|vertical|condor|butterfly|iron|credit|debit|collar|diagonal)\b",
    re.I)
OPTION_RE = re.compile(r"\b(call|put)s?\b|\d\s*[cp]\b", re.I)
# a strike in James's text: 2-5 digits with a C/P letter, or a bare 4-5 digit
# index strike, or a vertical pair 'X/Y'
STRIKE_RE = re.compile(
    r"(?<!\d)(\d{2,5}(?:\.\d+)?)\s*[CP]\b|\b\d{4,5}(?:\.\d+)?\b", re.I)
PAIR_RE = re.compile(r"\d{2,5}(?:\.\d+)?\s*/\s*\d{2,5}(?:\.\d+)?")
TICKER_RE = re.compile(r"\b[A-Z]{2,5}\b")
PREMIUM_RE = re.compile(r"\$\s*(\d+(?:\.\d+)?)|collect(?:ed|ing)?\s*\$?(\d+)", re.I)

EXIT_RE = re.compile(
    r"\b(take|book|trim|close)\s+(gains|profit|it|them|all)\b|\bpl\s+book\b", re.I)
HOLD_RE = re.compile(r"\b(holding|hold|held)\b", re.I)
LINK_RE = re.compile(r"https?://|youtube|zoom", re.I)
FUTURE_RE = re.compile(
    r"\b(will|going to|gonna|expect\w*|should|may|might|hoping)\b", re.I)

# D126 (2026-11-20): conditional trade GUIDANCE ('Take Overnight trades only if
# you can close them overnight') instructs FUTURE entries — it is not a close
# now. The verb's object is a CLASS of trades, not existing gains, and the
# message names no position to scope. This guard lives HERE (the canonical
# classifier) so the arrival tag (message_tag) and the trade path
# (james_pipe.non_entry_class) can never disagree about it — D121 put it only
# in the trade path and the arrival tag still read 4501 as an exit.
GUIDANCE_RE = re.compile(
    r"\b(?:only\s+if|if\s+you\s+can|whenever|as\s+long\s+as)\b.*?"
    r"\b(?:overnight\s+trades?|limit\s+order|entries|trades?)\b", re.I | re.S)


def is_guidance(text):
    """True for a conditional rule about HOW to trade, not a command to close."""
    return bool(GUIDANCE_RE.search(text or ""))


# D126: a bare 'trim' is a management command regardless of what follows it
# ('trim 80%', 'trim the runners'). The trade path has always read it that way
# (non_entry_class's first check); the arrival classifier did not, so a real
# trim was tagged 'narrative' on arrival — and narrative skips the LSE check.
TRIM_RE = re.compile(r"\btrim(?:s|med|ming)?\b", re.I)

# D121/D126: an EXPLICIT action verb is what separates a management COMMAND
# from a progress STATUS. '35% on bull puts' (4496) was a status — the real
# instruction came next ('trim 80%', 4497) — but the bare-% rule closed the
# whole position on the status. Only a message carrying one of these verbs may
# command a close; a bare '<n>% on <family>' becomes a lifecycle COMMENT.
# Canonical home: james_pipe delegates to THIS definition so the two can never
# drift apart (they did — D126).
ACTION_VERB = re.compile(
    r"\b(trim|cut|book(?:ed|ing)?|close(?:s|d|ing)?|cover(?:ed|ing)?|"
    r"tak(?:e|ing)|took|reduce(?:d|s)?|exit(?:s|ed|ing)?)\b", re.I)

# the credit/premium read: verb (collect/credit forms, incl. the D20 'coleect'
# misspelling) followed by a dollar figure, 'X to Y' range -> lower bound.
# This is the ONE regex for 'how much premium is stated' — text_credit used to
# carry its own copy (D20's original fix site) and drifted from the rest.
CREDIT_RE = re.compile(
    r"\b(?:collect(?:ed|ing)?|coleect|credit(?:ed|ing)?)"
    r"\s*(?:of\s*|around\s*|about\s*)?"
    r"\$?\s*(\d+(?:\.\d+)?)"
    r"(?:\s*(?:to)\s*\$?\s*(\d+(?:\.\d+)?))?",
    re.I)


def parse_credit(text):
    """Per-contract credit from the text, or None. 'collect $380' = $3.80 of
    premium (dollar-on-1-contract form); 'X to Y' ranges take the LOWER bound
    (conservative). Contract-count suffixes ('2KC','2C') must be stripped by
    the caller before this runs."""
    m = CREDIT_RE.search(str(text or ""))
    if not m:
        return None

    def _per_contract(v):
        f = float(v)
        return f / 100.0 if ("." not in v and f >= 1) else f

    lo = min(_per_contract(m.group(1)), _per_contract(m.group(2) or m.group(1)))
    return round(lo, 2)


def sell_count(text):
    return len(SELL_RE.findall(text or ""))


def buy_count(text):
    return len(BUY_RE.findall(text or ""))


def is_spread_language(text):
    return bool(SPREAD_RE.search(text or ""))


def direction(text):
    """'short' / 'long' / None for a SINGLE-LEG option intent. A message with
    BOTH a sell and a buy verb is a 2-leg structure (a spread) — its direction
    is decided by the spread path, so return None here. For a one-sided
    message, 'collected' adds a sell vote (short-credit) and 'paid' adds a buy
    vote (long-debit)."""
    t = text or ""
    if is_spread_language(t):
        return None
    s = sell_count(t)
    b = buy_count(t)
    # both sides present -> a paired sell+buy (spread), not a single-leg direction
    if s > 0 and b > 0:
        return None
    # reinforce the one-sided read with collect/paid
    if COLLECT_RE.search(t):
        s += 1
    if PAY_RE.search(t):
        b += 1
    if s > b and s > 0:
        return "short"
    if b > s and b > 0:
        return "long"
    return None


def has_strike(text):
    t = text or ""
    return bool(STRIKE_RE.search(t)) or bool(PAIR_RE.search(t))


def has_ticker(text):
    return bool(TICKER_RE.search(text or ""))


def has_premium(text):
    return bool(PREMIUM_RE.search(text or ""))


def entry_flavor(text):
    """How strongly does this message look like it is trying to place a trade?
    Returns 'clear' | 'unclear' | None.

    'clear'   -> a clean buy/sell/collect verb with a strike.
    'unclear' -> strike + ticker or premium, but no clean verb (the D110/D112
                 shape: 'Sold ... collected' used to land here and be misread).
    None      -> no trade shape at all (narrative, price talk, recap)."""
    t = text or ""
    if not (has_strike(t) or has_premium(t)):
        return None
    if SELL_RE.search(t) or BUY_RE.search(t) or COLLECT_RE.search(t):
        return "clear"
    # strike/ticker/premium present but no clean verb -> the unclear case
    return "unclear" if (has_ticker(t) or has_premium(t)) else None


def classify(raw):
    """The ONE arrival classifier. Replaces message_tag.classify's inline
    verb list. 'unclear' is its own category — never collapsed into narrative."""
    t = raw or ""
    if not t.strip():
        return "empty"
    if LINK_RE.search(t):
        return "link"
    # D126: guidance before EXIT_RE — 'close them overnight' matches the
    # 'close + them' exit pattern, but it is a conditional RULE about future
    # trades, not a command to close now.
    # D126: a bare 'trim' is a command first — matches the trade path's order
    # (non_entry_class checks \btrim\b before anything else).
    if TRIM_RE.search(t):
        return "exit"
    if is_guidance(t):
        return "narrative"
    if EXIT_RE.search(t):
        return "exit"
    if HOLD_RE.search(t):
        return "hold"
    flavor = entry_flavor(t)
    # D126: a CLEAR entry command wins even when it is future-tense
    # ('we will buy SPX 7300C tomorrow' is still an entry). Only an unclear
    # one defers to prediction — 'SPX will cut losses if it breaks 7300' is a
    # forecast that merely names a level (the trade path reads it the same way).
    if flavor == "clear":
        return "entry"
    if FUTURE_RE.search(t):
        return "prediction"
    if flavor == "unclear":
        return "unclear"
    # D121/D126 safety net (mirrors the trade path): an ACTION VERB + a
    # PERCENTAGE is a management command even when no strikes are named
    # ('cut 80%', 'cover 50%'). Deliberately NOT 'gains'/'out': those commands
    # are already caught by EXIT_RE's imperative form ('book gains', 'take
    # gains'), and the PAST-TENSE recap 'We took gains ... premarket' must stay
    # narrative (EXIT_RE already excludes 'took' for exactly this reason).
    if ACTION_VERB.search(t) and re.search(r"\d{1,3}\s*%", t):
        return "exit"
    return "narrative"


def fingerprint(text):
    """A stable structural key for prior-shape lookup: verb tense surface,
    structure shape, ticker class, and whether the direction was resolved."""
    t = text or ""
    verbs = set()
    for name, rx in (("sell", SELL_RE), ("buy", BUY_RE), ("collect", COLLECT_RE),
                     ("pay", PAY_RE)):
        if rx.search(t):
            verbs.add(name)
    ticker = None
    m = TICKER_RE.search(t)
    if m:
        ticker = m.group(0).upper()
        # ticker class via ticker_taxonomy when importable
        try:
            import ticker_taxonomy as _tx
            canon, bucket = _tx.normalise(ticker)
            ticker = f"{ticker}:{bucket}" if bucket in ("index", "etf") else ticker
        except Exception:
            pass
    return {
        "verbs": "+".join(sorted(verbs)) or "none",
        "spread": bool(PAIR_RE.search(t)) or is_spread_language(t),
        "ticker_class": ticker or "none",
        "direction": direction(t) or "unresolved",
        "flavor": entry_flavor(t) or "none",
    }


# D116 (2026-11-19): "enter the SAME bull puts again" — a re-entry INVITE with
# no fresh strike/premium that points BACK at a structure we already know.
# Returns a dict describing the back-reference, or None when the message does
# not point backwards.
SAME_RE = re.compile(
    r"\b(?:the\s+)?same\b|\b(?:again|re-?enter|re-?enter(?:ing|ed)?|add\s+the\s+same|"
    r"if\s+anyone\s+missed\b)",
    re.I)
SIDE_RE = re.compile(r"\b(bull|bear)\s+(put|call)s?\b", re.I)


def back_reference(text):
    """Does this message point at a prior structure instead of naming one?
    Returns {'side': 'bull|bear', 'right': 'put|call', 'ticker': ...} or None.
    The ticker is the index/etf named in the text, canonicalised by the caller."""
    t = text or ""
    if not SAME_RE.search(t):
        return None
    # a pure back-reference names NO option contract of its own. But a recap
    # like 'entered bull puts at 7320' carries a LEVEL, not a strike. The
    # difference: a back-reference's number is a narrative level (price action),
    # so we only refuse when there is an EXPLICIT contract (strike + C/P letter,
    # or a collect/premium). A bare 4-5 digit level is NOT a contract.
    if re.search(r"\d{2,5}\s*[CP]\b", t, re.I) or COLLECT_RE.search(t):
        return None            # names its own contract -> not a pure back-ref
    side = "bull"
    right = "put"
    m = SIDE_RE.search(t)
    if m:
        side, right = m.group(1).lower(), m.group(2).lower()
    else:
        if not TICKER_RE.search(t):
            return None
    tk = None
    _noise = {"US", "IF", "TO", "IN", "AT", "ON", "PL", "MK", "THE", "AND", "NOW",
              "FOR", "YOU", "CAN", "ADD", "SAME", "MISSED", "SESSION"}
    # prefer the first candidate that canonicalises to a REAL instrument
    for _cand in TICKER_RE.findall(t):
        _c = _cand.upper()
        if _c in _noise:
            continue
        try:
            import ticker_taxonomy as _tx
            _canon, _bucket = _tx.normalise(_c)
            if _bucket in ("index", "etf", "stock"):
                tk = _canon
                break
        except Exception:
            tk = _c
            break
    return {"side": side, "right": right, "ticker": tk}
