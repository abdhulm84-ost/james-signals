"""
james_core.py — clean core for the James signal pipeline (v2).

Rebuilt around the model the message review established:
    message -> TYPED RECORD -> managed STRUCTURE -> correlated PORTFOLIO
    -> risk-controlled EXECUTION.

This module holds the PURE, TESTABLE logic (no I/O, no broker calls):
  - Record        : the typed unit a message becomes
  - Structure     : a trade with legs and its own lifecycle
  - RiskPolicy    : the gate (Phase 1/2) + sizing
  - ExitEngine    : resolves every exit form to a concrete action
  - ScopeResolver : matches exits to positions (named | scope | ladder)

Broker calls (Tradier/BlackBull/Tiger) live behind the Executor protocol in
james_exec.py and are NOT touched here. Image extraction lives in
james_images.py. Both are wired in at integration, not in this core.

Design decisions recorded in james_parser_spec.md and james_system_limitations.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------- record model

class MsgClass(str, Enum):
    MAP = "map"
    OPTION_BRACKET = "option_bracket"
    VERTICAL = "vertical"
    FLY = "fly"
    CONDOR = "condor"
    NAKED = "naked"
    LOTTO = "lotto"
    DECAY = "decay"
    EXIT = "exit"
    SIZE = "size"
    PREDICTION = "prediction"
    GOAL = "goal"
    IGNORE = "ignore"
    NARRATIVE = "narrative"
    UNPARSABLE = "unparsable"


class RiskTier(str, Enum):
    NORMAL = "normal"
    HIGH_RISK = "high_risk"
    LOTTO = "lotto"


class Action(str, Enum):
    ENTRY = "entry"
    EXIT = "exit"
    HOLD = "hold"
    TRIM = "trim"
    IGNORE = "ignore"
    NONE = "none"


@dataclass
class Leg:
    strike: float
    right: str              # "call" | "put"
    action: str             # "buy" | "sell"  (to open)
    qty: int = 1
    underlying: str = ""    # for OCC symbol
    expiry: str = ""        # YYYY-MM-DD for OCC symbol

    def occ(self) -> str:
        """OCC symbology: ROOT YYMMDD C/P STRIKE*1000  (e.g. AMZN 261120C00255000)"""
        yymmdd = self.expiry.replace("-", "")[2:] if self.expiry else "000000"
        # OCC symbology has NO space: SPY261106C00555000 (Tradier rejects a space)
        return f"{self.underlying}{yymmdd}{self.right[0].upper()}{int(round(self.strike*1000)):08d}"


@dataclass
class Record:
    """One message, faithfully extracted. Never a trade on its own."""
    msg_id: int
    ts: str
    cls: MsgClass
    action: Action
    raw: str
    source: str = "text"                  # text | chart_text | screenshot | screenshot_text

    underlying: Optional[str] = None
    underlying_class: Optional[str] = None   # index_option|equity_option|future|cfd|etf
    direction: str = "long"                   # long | short (for single-leg side)
    account: Optional[str] = None            # rh | tradier | funded | personal | unknown
    sector: Optional[str] = None

    structure_type: Optional[str] = None
    body: Optional[float] = None
    wing_low: Optional[float] = None
    wing_high: Optional[float] = None
    body_wing: Optional[float] = None        # L19: the RISK width for flies
    width: Optional[float] = None
    legs: list = field(default_factory=list)

    credit: Optional[float] = None           # +credit, -debit, per contract
    qty: Optional[int] = None
    expiry: Optional[str] = None
    entry_price: Optional[float] = None
    stop: Optional[float] = None
    targets: list = field(default_factory=list)

    risk_stated: Optional[float] = None
    reward_stated: Optional[float] = None
    pct_claimed: Optional[float] = None
    pct_actual: Optional[float] = None
    grade: Optional[str] = None              # L34: A+/A/B...
    risk_tier: RiskTier = RiskTier.NORMAL

    exit_by: Optional[dict] = None           # {type: price|time|event|session|in_profit|mechanism, ...}
    scope: Optional[dict] = None             # {time_window, structure, sector, expiry, ...}
    thesis: Optional[str] = None             # chop/decay/iv/earnings (L41)

    missing: list = field(default_factory=list)
    _inferred: bool = False

    @property
    def credit_pct_width(self) -> Optional[float]:
        """L-quality metric: predicts R:R. High = favourable."""
        if self.credit is None or not self.width:
            return None
        return self.credit / self.width

    @property
    def reward_risk(self) -> Optional[float]:
        if self.credit is None or not self.width:
            return None
        max_p = self.credit * 100
        max_l = (self.width - self.credit) * 100
        return max_p / max_l if max_l else None

    def derive_width_from_risk_reward(self) -> Optional[float]:
        """L38: risk/reward line reconstructs unstated wing width."""
        if self.risk_stated is None or self.credit is None:
            return None
        return round(self.risk_stated / 100 + self.credit, 2)


# ---------------------------------------------------------------- text helpers

def text_strikes(text: str) -> list[float]:
    t = text or ""
    found = set()
    for m in re.findall(r"(?<!\d)(\d{2,5}(?:\.\d+)?)\s*[CP]\b", t, re.I):
        found.add(float(m))
    for m in re.findall(r"(?<!\d)(\d{4,5}(?:\.\d+)?)(?!\d)", t):
        found.add(float(m))
    # vertical "X/Y" strike pairs ("32.5/37.5", "78/83"); skip date-like MM/DD.
    for m in re.finditer(r"(?<!\d)(\d{2,5}(?:\.\d+)?)\s*/\s*(\d{2,5}(?:\.\d+)?)(?!\d)", t):
        a, b = float(m.group(1)), float(m.group(2))
        if a == int(a) and b == int(b) and 1 <= a <= 12 and 1 <= b <= 31:
            continue
        found.add(a)
        found.add(b)
    return sorted(found)


def has_command(text: str) -> bool:
    return bool(re.search(r"\b(buy|sell|collect|enter)\w*\b", text or "", re.I))


def anchor_close_cap(legs, spot, tv_pct: float = 0.25):
    """D140 (2026-11-21): real-spot upper bound on what a 2-leg credit
    vertical can be worth — the SOLD leg's intrinsic plus a time-value
    allowance (tv_pct of the width, floored at 1.0 for narrow spreads).
    A close priced above this bound is only explicable as a bad print:
    Friday's legging filled 9.31 net on a 2-wide, 30.40 on a 15-wide, both
    beyond any price the package can legally reach.

    Bound: package = sold_leg_value - bought_leg_value <= sold_leg_value =
    intrinsic + extrinsic, with the allowance standing in for the sold leg's
    extrinsic. Deep-ITM floors at the width — intrinsic alone can exceed it
    and the width is the structural maximum of a vertical.

    Returns None when the shape isn't exactly one sell + one buy of the same
    right (callers fall back to their mark band — an anchor never blocks on a
    shape it cannot prove)."""
    if not legs or len(legs) != 2 or spot is None:
        return None
    try:
        def _kra(l):
            if isinstance(l, dict):
                return (float(l.get("strike")),
                        str(l.get("right") or "?")[0].lower(),
                        str(l.get("action") or "").lower())
            return (float(l[0]), str(l[1])[0].lower(), str(l[2]).lower())
        (k1, r1, a1), (k2, r2, a2) = (_kra(l) for l in legs)
        if r1 != r2 or (a1[:1] == "s") == (a2[:1] == "s"):
            return None                      # mixed rights or not one sell+one buy
        S = float(spot)
        width = abs(k1 - k2)
        if width <= 0:
            return None
        sold_k = k1 if a1.startswith("s") else k2
        intr = (max(0.0, S - sold_k) if r1 == "c"
                else max(0.0, sold_k - S))
        tv = max(tv_pct * width, 1.0)
        return round(min(width, intr + tv), 2)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- risk policy

@dataclass
class RiskPolicy:
    """spec §10. Phase 1 = record all, trade only defined risk. Phase 2 = lottos."""
    phase: int = 1
    quality_sizing_threshold: float = 0.55   # credit/width above this -> full size
    max_same_expiry_risk: float = 5000.0     # portfolio cap (L22)
    max_position_risk: float = 1000.0

    def defined_risk(self, r: Record) -> bool:
        """Is the loss CAPPED? A long option loses only its premium, a spread at
        most its width, a long asset its notional. A NAKED write (cls=NAKED) or a
        lotto is uncapped/binary — always blocked. This is the hard gate: risk
        TYPE, not quality. (cls=NAKED is set only for real naked shorts; a long
        option is OPTION_BRACKET and passes below.)"""
        if r.cls == MsgClass.LOTTO:
            return False                      # binary; Phase 2 only
        if r.cls == MsgClass.NAKED:
            # cash ASSET with no strike: a long loses only to zero (capped), a
            # short asset is unbounded (msg 4017 "buy Long on NQ and ES" was
            # blocked as naked before this)
            if not r.legs:
                return r.direction == "long"
            if r.direction == "long":
                return True               # long option: premium is the whole risk
            # a naked short PUT is bounded by the strike (cash-secured);
            # only naked CALLS are genuinely unbounded
            return all(str(getattr(l, "right", "")).lower().startswith("p")
                       for l in r.legs)
        if r.width is not None or r.stop is not None:
            return True                       # spread width or a stop caps loss
        # No width/stop: a LONG option/asset still loses only what it paid.
        return r.direction == "long"

    def allow(self, r: Record) -> tuple[bool, str]:
        """Hard gate: risk TYPE, not quality. Never reject valid defined risk."""
        if r.cls in (MsgClass.MAP, MsgClass.PREDICTION, MsgClass.NARRATIVE,
                     MsgClass.DECAY, MsgClass.GOAL, MsgClass.SIZE, MsgClass.IGNORE):
            return False, f"{r.cls.value} is context, not a trade"
        if r.cls == MsgClass.LOTTO:
            return (False, "lotto: record-only until Phase 2") if self.phase == 1 \
                else (True, "lotto allowed in Phase 2")
        if not self.defined_risk(r):
            return False, "undefined risk (naked) — policy blocks"
        return True, "defined risk — allowed"

    def size(self, r: Record, buying_power: float, base_notional: float = 1000.0) -> float:
        """Size by QUALITY (L38 metric) + risk tier, capped by buying power (L39).
        Downsize, never drop (spec §10 balance guard)."""
        if r.credit_pct_width is None:
            f = 0.5
        else:
            f = 1.0 if r.credit_pct_width >= self.quality_sizing_threshold else 0.5
        if r.risk_tier == RiskTier.HIGH_RISK:
            f *= 0.5
        elif r.risk_tier == RiskTier.LOTTO:
            f *= 0.25
        if r.grade in ("A+",):
            f = min(1.25, f * 1.25)
        notional = base_notional * f
        # never exceed buying power (sizing cap, L39)
        return max(1.0, min(notional, buying_power * 0.25))


# ---------------------------------------------------------------- exit engine

class ExitType(str, Enum):
    PRICE = "price"
    TIME = "time"
    EVENT = "event"
    SESSION = "session"
    IN_PROFIT = "in_profit"
    MECHANISM = "mechanism"
    TARGET_LADDER = "target_ladder"
    SCOPED_TRIM = "scoped_trim"
    EXPLICIT_CLOSE = "explicit_close"
    RESTING_LIMIT = "resting_limit"
    DISCRETIONARY_STOP = "discretionary_stop"


@dataclass
class ExitAction:
    kind: ExitType
    position_ref: str          # id | scope-key | "all"
    close_fraction: float      # 0..1
    limit_price: Optional[float] = None
    deadline: Optional[str] = None
    venue_session: Optional[str] = None
    note: str = ""


class ExitEngine:
    """Resolves every exit form to a concrete ExitAction.

    L27 rule: explicit close instructions are executed VERBATIM; everything else
    is inferred via ScopeResolver. Never guess (spec §10 balance guard).
    """

    def resolve(self, rec: Record, positions: list) -> tuple[Optional[ExitAction], str]:
        if rec.cls != MsgClass.EXIT:
            return None, "not an exit"

        # 1. explicit close order (highest fidelity)  L27
        if rec.legs and all(getattr(l, "action", "") in ("buy_to_close", "sell_to_close")
                            for l in rec.legs):
            return ExitAction(ExitType.EXPLICIT_CLOSE, rec.underlying or "named",
                              1.0, note="explicit close order, execute verbatim"), "explicit"

        # 2. scoped trim ("Trim 80% on today's bear calls")  L15/L32/L37
        if rec.scope:
            frac = (rec.pct_claimed or 0) / 100 if rec.pct_claimed else 1.0
            return ExitAction(ExitType.SCOPED_TRIM, f"scope:{rec.scope}",
                              max(0.0, min(1.0, frac)), note="scoped group trim"), "scope"

        # 3. target-ladder partial ("1st target done")  L21
        if rec.cls == MsgClass.EXIT and rec.targets and rec.action == Action.TRIM:
            return ExitAction(ExitType.TARGET_LADDER, rec.underlying or "matched",
                              1.0 / max(1, len(rec.targets)), note="target-ladder partial"), "ladder"

        # 4. resting limit exit  L23
        if rec.exit_by and rec.exit_by.get("type") == "limit":
            return ExitAction(ExitType.RESTING_LIMIT, rec.underlying or "named", 1.0,
                              limit_price=rec.exit_by.get("price")), "resting"

        # 5. time / session / event  L18/L43
        if rec.exit_by and rec.exit_by.get("type") in ("time", "session", "event"):
            return ExitAction(ExitType.TIME if rec.exit_by["type"] == "time" else ExitType.SESSION,
                              rec.underlying or "named", 1.0,
                              deadline=rec.exit_by.get("at"),
                              venue_session=rec.exit_by.get("venue")), "time"

        # 6. conditional "when green"  L35
        if rec.exit_by and rec.exit_by.get("type") == "in_profit":
            return ExitAction(ExitType.IN_PROFIT, rec.underlying or "named", 1.0,
                              note="when position is in profit"), "in_profit"

        # 7. discretionary stop-loss  L36
        if rec.exit_by and rec.exit_by.get("type") == "stop_loss":
            return ExitAction(ExitType.DISCRETIONARY_STOP, rec.underlying or "named",
                              1.0, note="discretionary cut"), "stop"

        # fallback: plain full exit on a named position
        return ExitAction(ExitType.PRICE, rec.underlying or "named", 1.0,
                          note="plain exit"), "named"


# ---------------------------------------------------------------- scope resolver

class ScopeResolver:
    """Matches an exit to positions. §4.7 + L15/L32/L37.

    Guiding principle: a wrong trade is worse than a missed one. If the match is
    ambiguous and the scope does not itself specify the set, ALERT and do nothing.
    """

    def match(self, exit_scope: dict, positions: list) -> tuple[list, str]:
        if not exit_scope:
            return [], "no scope"
        out = [p for p in positions if self._fits(p, exit_scope)]
        if len(out) == 0:
            return [], "no matching position"
        if len(out) == 1:
            return out, "single match"
        # multiple: the scope IS the specification -> act on the set
        return out, f"scoped set ({len(out)})"

    @staticmethod
    def _fits(p, scope: dict) -> bool:
        for k in ("structure_type", "direction", "underlying_class", "sector", "expiry"):
            if scope.get(k) and getattr(p, k, None) != scope[k]:
                return False
        if scope.get("time_window") and not getattr(p, "opened_today", True):
            return False
        return True

    def match_ladder(self, rec: Record, positions: list) -> tuple[Optional[object], str]:
        """§4.7: 'Nth target done' -> price-match then recency; alert if ambiguous."""
        n = len(rec.targets) or 1
        cands = [p for p in positions if len(getattr(p, "targets", []) or []) >= n]
        if not cands:
            return None, "no target-bearing position"
        if len(cands) == 1:
            return cands[0], "single ladder match"
        # price-match: whose targets[n-1] is breached by live price?
        # (needs a live price; if unavailable -> ambiguous -> alert)
        return None, "AMBIGUOUS — alert, do not trade"
