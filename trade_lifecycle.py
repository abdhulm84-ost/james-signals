#!/usr/bin/env python3
"""trade_lifecycle.py — per-trade narrative tracker (2026-11-19).

The user's proposal: when a narrative arrives that describes a trade direction,
open a lifecycle ID. As subsequent messages reference that trade (trim, add,
exit, commentary), append to the same ID. When the narrative closes (expiry,
full exit, or explicit "done"), mark the outcome. Each trade becomes a
readable story instead of scattered ledger rows.

Storage: trade_lifecycle.csv — append-only, one row per event.
Columns: ts, lifecycle_id, msg_id, event, instrument, detail, raw

A lifecycle_id is assigned when:
  * a trade is opened (ORDER/PENDING) — the trade_id becomes the lifecycle_id
  * a narrative/prediction mentions a specific structure — a new LC-<msg_id> is created
  * a re-send/add references an existing trade — it joins that trade's lifecycle

Events in a lifecycle:
  OPEN     -> trade placed or narrative identified
  ADD      -> re-send / "add more" / "same again"
  TRIM     -> partial close ("trim 80%", "take 40%")
  EXIT     -> full close
  COMMENT  -> narrative/commentary about the trade
  OUTCOME  -> final PnL or would-have PnL
  SHADOW   -> counterfactual tracking

Advisory + archive only: never trades, never blocks. The lifecycle is a
READ layer over the existing ledger — it doesn't change any trade decision.
"""
import csv
import pathlib
import re
import time

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "trade_lifecycle.csv"
COLS = ["ts", "lifecycle_id", "msg_id", "event", "instrument", "detail", "raw"]


def _is_test_run():
    """D127 (2026-11-20): the D102/D107 lesson, third instance — this archive
    had NO test isolation, so every suite run that drove process_message wrote
    fixture lifecycles (TL1/TL953/TL961...) into the LIVE trade_lifecycle.csv:
    576 fake rows. Under a test module the archive is a throwaway file."""
    import sys as _sys
    argv0 = (_sys.argv[0] or "").lower()
    return "test_" in argv0 or "_test" in argv0 or "pytest" in argv0


def _out_path():
    """Where to read/write. Honours a caller's redirect of OUT (tests point it
    at a temp file), otherwise routes by test context."""
    if OUT != HERE / "trade_lifecycle.csv":
        return OUT                      # caller redirected — use it as-is
    return (HERE / "trade_lifecycle.test.csv") if _is_test_run() else OUT


def _append(row):
    out = _out_path()
    new = not out.exists()
    with out.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        if new:
            w.writeheader()
        w.writerow(row)


def record(lifecycle_id, msg_id, event, instrument="", detail="", raw=""):
    """Append one event to a trade's lifecycle. Returns the lifecycle_id so
    callers can chain (D128: every writer used to return None, so
    comment_lifecycle's id never reached related_and_link and link() refused
    the falsy id — the archive had ZERO LINK rows while the log claimed
    'lifecycle None linked -> LC-4511')."""
    _append({
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "lifecycle_id": str(lifecycle_id),
        "msg_id": str(msg_id),
        "event": event,
        "instrument": instrument or "",
        "detail": detail or "",
        "raw": (raw or "")[:120],
    })
    return lifecycle_id


def open_lifecycle(trade_id, msg_id, instrument, detail="", raw=""):
    """A trade was opened — start its lifecycle."""
    return record(trade_id, msg_id, "OPEN", instrument, detail, raw)


def add_to_lifecycle(trade_id, msg_id, detail="", raw=""):
    """A re-send / add-more / same-again joined this trade."""
    return record(trade_id, msg_id, "ADD", detail=detail, raw=raw)


def trim_lifecycle(trade_id, msg_id, detail="", raw="", instrument=""):
    """A partial close was recorded. instrument is optional (D128: passing it
    keeps a closed trade linkable — find_related matches on it)."""
    return record(trade_id, msg_id, "TRIM", instrument, detail, raw)


def exit_lifecycle(trade_id, msg_id, detail="", raw="", instrument=""):
    """The trade was fully closed. instrument optional — see trim_lifecycle."""
    return record(trade_id, msg_id, "EXIT", instrument, detail, raw)


def comment_lifecycle(lifecycle_id, msg_id, instrument="", detail="", raw=""):
    """A narrative/comment about a trade (may or may not have a trade_id yet)."""
    return record(lifecycle_id, msg_id, "COMMENT", instrument, detail, raw)


def outcome_lifecycle(trade_id, msg_id, detail="", raw=""):
    """Final PnL or would-have PnL recorded."""
    return record(trade_id, msg_id, "OUTCOME", detail=detail, raw=raw)


def shadow_lifecycle(trade_id, msg_id, detail="", raw=""):
    """Counterfactual / shadow tracking."""
    return record(trade_id, msg_id, "SHADOW", detail=detail, raw=raw)


def narrative_lifecycle(msg_id, instrument, detail="", raw=""):
    """A narrative/prediction that describes a trade direction but isn't a
    trade yet. Gets its own lifecycle ID (LC-<msg_id>) so future messages
    can join it."""
    lc_id = f"LC-{msg_id}"
    record(lc_id, msg_id, "OPEN", instrument, detail, raw)
    return lc_id


def link(lifecycle_id, to_id, reason="", msg_id="", src_ts=""):
    """D123: record that two lifecycles are related — a compound/compounding
    message ('Take Overnight trades only if...') that continues an earlier
    thread, or a narrative that follows a structure. The relationship is
    directional: this lifecycle continues the prior one. Idempotent — the same
    link is recorded once, not per re-process.

    D128: returns True when a LINK row now exists for this pair (written now
    or already present), False when refused (falsy id / self-link) — so
    callers never log 'linked' for a link that was never written.

    2026-11-20 (user note): a relationship also captures WHO/WHEN established
    it — the linking message's id goes in the msg_id column and its timestamp
    is appended as `[src ...]` in detail, so the trail shows when the linking
    message arrived, not just when the row happened to be written."""
    if not lifecycle_id or not to_id or str(lifecycle_id) == str(to_id):
        return False
    needle = f"-> {to_id}"
    for e in get_lifecycle(lifecycle_id):
        if e.get("event") == "LINK" and str(e.get("detail") or "").startswith(needle):
            return True                   # already linked — do not thicken
    detail = needle + (f": {reason}" if reason else "")
    if src_ts:
        detail += f" [src {src_ts}]"
    record(lifecycle_id, str(msg_id or ""), "LINK", detail=detail, raw="")
    return True


def _canon(instr):
    u = str(instr or "").upper().strip()
    if not u:
        return ""
    try:
        import ticker_taxonomy as _tx
        return _tx.canonical_of(u) or u.split()[0]
    except Exception:
        return u.split()[0]


def find_related(instrument, exclude_id="", limit_days=7):
    """The most recent OTHER lifecycle for the same canonical instrument, within
    `limit_days`. Returns its lifecycle_id or None. Used to chain a new
    narrative onto the structure it is talking about ('the remaining 20% of
    7315/7325 Bull Puts' -> the trade's own lifecycle)."""
    if not _out_path().exists():
        return None
    want = _canon(instrument)
    if not want:
        return None
    rows = list(csv.DictReader(_out_path().open(encoding="utf-8")))
    if not rows:
        return None
    import datetime as _dt
    cutoff = (_dt.datetime.now() - _dt.timedelta(days=limit_days)).strftime("%Y-%m-%d")
    best = None
    for r in rows:                      # rows are append-ordered: last wins
        if str(r.get("lifecycle_id")) == str(exclude_id):
            continue
        ts = str(r.get("ts") or "")
        if ts[:10] < cutoff:
            continue
        if _canon(r.get("instrument")) == want:
            best = r.get("lifecycle_id")   # keep scanning; last match wins
    return best


def related_and_link(lifecycle_id, instrument, reason="", msg_id="", src_ts=""):
    """Convenience: find a related lifecycle for `instrument` and, if found,
    record the LINK. Returns the linked id ONLY when a LINK row exists for the
    pair (D128: used to return the target even when link() refused the call,
    so the pipe logged links that were never written). Never raises.
    msg_id/src_ts ride through to link() (2026-11-20 timestamp capture)."""
    try:
        tgt = find_related(instrument, exclude_id=lifecycle_id)
        if tgt and link(lifecycle_id, tgt, reason, msg_id=msg_id, src_ts=src_ts):
            return tgt
        return None
    except Exception:
        return None


def get_lifecycle(lifecycle_id):
    """Read all events for a lifecycle, in order."""
    if not _out_path().exists():
        return []
    rows = list(csv.DictReader(_out_path().open(encoding="utf-8")))
    return [r for r in rows if r["lifecycle_id"] == str(lifecycle_id)]


def report(lifecycle_id=None):
    """Print a lifecycle (or all lifecycles) as a readable story."""
    if not _out_path().exists():
        print("no trade_lifecycle.csv yet")
        return 0
    rows = list(csv.DictReader(_out_path().open(encoding="utf-8")))
    if lifecycle_id:
        rows = [r for r in rows if r["lifecycle_id"] == str(lifecycle_id)]
    if not rows:
        print(f"no lifecycle found for {lifecycle_id}")
        return 0
    # group by lifecycle_id
    by_lc = {}
    for r in rows:
        by_lc.setdefault(r["lifecycle_id"], []).append(r)
    for lc_id, events in sorted(by_lc.items()):
        print(f"\n=== {lc_id} ({len(events)} events) ===")
        for e in events:
            print(f"  {e['ts']} [{e['event']:<8}] msg{e['msg_id']:<5} "
                  f"{e['instrument']:<8} {e['detail'][:60]}")
    return 0


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        report(sys.argv[1])
    else:
        report()
