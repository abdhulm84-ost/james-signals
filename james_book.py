"""
james_book.py — the POSITION BOOK and WATCHLIST.

Design principle (from the channel analysis): EVERY message is bookkeeping.
James's feed is a running position-management log, not a stream of standalone
signals. "40%", "Take gains on 7720/7730", "Trim 80%", "1st target done" are all
BOOK OPERATIONS on positions we should already hold. Treated as noise they look
unactionable; treated as bookkeeping they resolve against the watchlist.

So the model is:
  message -> book operation (OPEN / TRIM / CLOSE / MARK / WATCH / CONTEXT)
  positions live in a WATCHLIST with live state
  a message is ACTIONABLE when it reasonably acts on the book (entry with
  defined risk, exit when we hold it, trim when marked).

The channel does not restate positions or size — it assumes continuity. The book
supplies that context; the screenshots supply qty + entry basis.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class PosState(str, Enum):
    WATCH = "watch"        # parsed, not taken (parked / risky / untradeable)
    OPEN = "open"          # taken, live
    TRIMMED = "trimmed"    # partially closed
    CLOSED = "closed"      # fully out
    MISSED = "missed"      # would have worked, we couldn't take it


class Op(str, Enum):
    OPEN = "open"
    TRIM = "trim"
    CLOSE = "close"
    MARK = "mark"
    WATCH = "watch"
    CONTEXT = "context"


@dataclass
class Position:
    pid: str
    msg_id: int
    ts: str
    structure: str = "unknown"       # vertical | fly | bracket | naked | lotto
    underlying: str = ""
    body: Optional[float] = None
    wing_low: Optional[float] = None
    wing_high: Optional[float] = None
    credit: Optional[float] = None
    qty: Optional[int] = None        # contracts/lots (image-only usually)
    expiry: Optional[str] = None
    account: Optional[str] = None    # rh | tradier | funded | unknown
    state: PosState = PosState.WATCH
    qty_remaining: Optional[int] = None
    marks: list = field(default_factory=list)   # pct marks over life
    note: str = ""
    closed_ts: Optional[str] = None

    @property
    def is_open(self) -> bool:
        return self.state in (PosState.OPEN, PosState.TRIMMED)

    @property
    def risk(self) -> Optional[float]:
        """max loss in $ (width-credit)*100*qty, if known."""
        if self.credit is None or self.body_wing is None or not self.qty_remaining:
            return None
        return round((self.body_wing - self.credit) * 100 * self.qty_remaining, 2)

    @property
    def body_wing(self) -> Optional[float]:
        if self.body is None or (self.wing_low is None and self.wing_high is None):
            return None
        w = [x for x in (self.wing_low, self.wing_high) if x is not None]
        return round(max(abs(self.body - x) for x in w), 2) if w else None

    @property
    def quality(self) -> Optional[float]:
        """credit/width — the validated predictor of reward:risk."""
        bw = self.body_wing
        return round(self.credit / bw, 3) if (self.credit and bw) else None


class Book:
    """The portfolio + watchlist. Every message becomes an operation here."""

    def __init__(self):
        self.positions: dict[str, Position] = {}
        self._seq = 0

    def _new(self, **kw) -> Position:
        self._seq += 1
        p = Position(pid=f"P{self._seq:04d}", **kw)
        self.positions[p.pid] = p
        return p

    # ---- book operations (one per message) ----
    def open(self, msg_id, ts, **kw) -> Position:
        p = self._new(msg_id=msg_id, ts=ts, **kw)
        p.state = PosState.WATCH if kw.pop("_watch", False) else PosState.OPEN
        p.qty_remaining = p.qty
        return p

    def close(self, match_fn, ts) -> list[Position]:
        out = []
        for p in self.positions.values():
            if p.is_open and match_fn(p):
                p.state = PosState.CLOSED
                p.closed_ts = ts
                out.append(p)
        return out

    def trim(self, match_fn, fraction: float, ts) -> list[Position]:
        out = []
        for p in self.positions.values():
            if p.is_open and match_fn(p):
                p.marks.append({"ts": ts, "trim": fraction})
                if p.qty_remaining:
                    p.qty_remaining = max(1, round(p.qty_remaining * (1 - fraction)))
                if fraction >= 1:
                    p.state = PosState.CLOSED
                    p.closed_ts = ts
                else:
                    p.state = PosState.TRIMMED
                out.append(p)
        return out

    def mark(self, match_fn, pct: float, ts) -> list[Position]:
        out = []
        for p in self.positions.values():
            if p.is_open and match_fn(p):
                p.marks.append({"ts": ts, "pct": pct})
                out.append(p)
        return out

    def miss(self, pid: str):
        if pid in self.positions:
            self.positions[pid].state = PosState.MISSED

    # ---- views ----
    def watchlist(self) -> list[Position]:
        """Open positions to watch (for exits/trims). Sorted newest first."""
        return sorted([p for p in self.positions.values() if p.is_open],
                      key=lambda p: p.ts, reverse=True)

    # ---- persistence ----
    def to_dict(self) -> dict:
        import dataclasses
        out = {"seq": self._seq, "positions": []}
        for pos in self.positions.values():
            d = dataclasses.asdict(pos)
            d["state"] = pos.state.value
            out["positions"].append(d)
        return out

    @classmethod
    def from_dict(cls, d: dict) -> "Book":
        import dataclasses
        fields = {f.name for f in dataclasses.fields(Position)}
        b = cls()
        b._seq = d.get("seq", 0)
        for pd in d.get("positions", []):
            pd = dict(pd)
            pd["state"] = PosState(pd.get("state", "watch"))
            b.positions[pd["pid"]] = Position(**{k: v for k, v in pd.items() if k in fields})
        return b

    def summary(self) -> dict:
        from collections import Counter
        c = Counter(p.state.value for p in self.positions.values())
        wl = self.watchlist()
        return {
            "by_state": dict(c),
            "open_risk": round(sum(p.risk or 0 for p in wl), 2),
            "open_quality": [p.quality for p in wl],
            "by_account": dict(Counter(p.account for p in wl)),
        }


def message_to_op(text: str, cls: str) -> Op:
    """Classify a message as a bookkeeping operation (from its language).

    The rule: EVERY message is bookkeeping. An entry opens; an exit closes;
    a % / 'trim' marks or trims; a position-reference without action is a WATCH;
    anything else is CONTEXT (still recorded).
    """
    t = (text or "").lower()
    # proximity "close to $306" is not a close-the-position command
    t = re.sub(r"\bclose\s+to\b", "near", t)
    if re_search(r"\b(sell|buy|collect|enter)\w*\b", t) and re_search(
            r"\d{2,5}\s*[cp]|\bfor\s+\d|collect", t):
        if re_search(r"\b(to close|buy back|sell to close)\b", t):
            return Op.CLOSE
        return Op.OPEN
    # '<n>% on' = the profit MARK, not a size -> full close (CASE 1-10:
    # '100% on' closes out; '95% on QQQ bear calls, pl book gains' too).
    if re_search(r"\d{1,3}\s*%\s*(?:on|up)\b", t):
        return Op.CLOSE
    # trim = partial management (NOT a full close). Must be checked before the
    # close verbs, or "trim 80% and hold 20%" books as a full exit.
    if re_search(r"\btrim\b", t):
        return Op.TRIM
    # "book/close 80%" = a partial (trim). A bare "%" in a forecast ("will make
    # 100%") is NOT management — require a management verb to pair with it.
    mgmt = re_search(r"\b(tak(?:e|en|ing)|took|book|booked|close|exit|cut)\w*", t)
    pct = re_search(r"\b(\d{1,3})\s*%", t)
    if mgmt and pct:
        return Op.TRIM
    # verb-anchored range without a % sign ('take gains 2t/35') = partial trim
    if mgmt and re_search(r"\d{1,3}[a-z]?\s*/\s*\d{1,3}\b(?!\s*%)", t):
        return Op.TRIM
    if re_search(r"\b(tak(?:e|ing) gains|book gains|book the|book (?:it|them|out|all)|took gains|took profits|close out|close it|close them|booking|exit|cut)\b", t):
        return Op.CLOSE
    if re_search(r"\bholding|hold\b", t):
        return Op.WATCH
    return Op.CONTEXT


def re_search(pat, s):
    import re
    return re.search(pat, s or "")
