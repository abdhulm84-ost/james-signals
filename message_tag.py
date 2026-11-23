#!/usr/bin/env python3
"""message_tag.py — arrival-time tagging with flow context (2026-11-18).

User design: as soon as a message arrives, tag it with (a) what we can do with
it, and (b) when it is entry-shaped, the LSE flow verdict. The pipeline is idle
off-hours, so spend LSE calls freely there; during market hours keep it tight.

The rule (user, 2026-11-18):
  off-hours   -> LSE on EVERY entry-shaped message (any underlying)
  market hours-> LSE on SPX entry-shaped messages only
  never       -> LSE on narrative / exit / link / emoji-only (waste + noise)

Advisory + archive only: writes message_tags.csv; never trades, never blocks.
LSE runs OFF-THREAD so the serial queue is never stalled by a flow call.

  python message_tag.py --backfill   # tag recent messages in the ledger
  python message_tag.py --report     # read the tag archive
"""
import argparse
import csv
import datetime
import pathlib
import re
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
OUT = HERE / "message_tags.csv"
COLS = ["ts", "msg_id", "underlying", "classification", "flow_check",
        "flow_verdict", "suggested_action", "raw"]


def _is_test_run():
    """D107 (2026-11-19): the D102 guard sent test log lines to a throwaway
    file, but tag() still wrote into the LIVE message_tags.csv - so every suite
    run that drives process_message (fixture ids 901-999, X9, TAG1...) injected
    fake rows into the production archive. Same leak class, one layer deeper."""
    argv0 = (sys.argv[0] or "").lower()
    return "test_" in argv0 or "_test" in argv0 or "pytest" in argv0


def _out_path():
    return (HERE / "message_tags.test.csv") if _is_test_run() else OUT


def classify(raw):
    """What KIND of message is this, before any flow check.

    D113 (2026-11-19): delegates to signal_intent — the ONE canonical verb
    lexicon. A message with a strike/ticker/premium but NO clean verb is
    'unclear', NOT 'narrative' — unclear still gets the LSE flow check (see
    should_lse), because the tape resolves an unclear short-vs-long read.

    NO fallback verb list here: a hard dependency. A stale shadow copy is
    exactly how D110/D112 drifted — fail loudly instead."""
    import signal_intent as _si
    return _si.classify(raw)


def lse_flow(underlying, lookback_days=1):
    """Per-print put premium at/below a context strike is caller's job; here we
    just return the raw flow so the tag can carry the direction. Returns
    (put_premium, call_premium) for the window."""
    import re as _re
    import lse
    env = pathlib.Path(__import__("os").environ.get("LOCALAPPDATA", "")) \
        / "hermes/.env"
    m = _re.search(r"LSE_API_KEY=(.+)",
                   env.read_text(encoding="utf-8", errors="replace"))
    if not m:
        return None
    key = m.group(1).strip().strip('"').strip("'")
    c = lse.LSE(key)
    # D108: anchor the window to the US session, NOT the host clock. The host
    # runs NZDT (a day ahead of ET), so date.today() pointed at a future date
    # with no prints — every verdict came back "put $0 vs call $0".
    from zoneinfo import ZoneInfo
    end = datetime.datetime.now(ZoneInfo("America/New_York")).date()
    start = (end - datetime.timedelta(days=lookback_days)).isoformat()
    end = end.isoformat()
    rows = c.options_flow(underlying=underlying, start=start, end=end,
                          min_premium=20000, limit=200)
    pp = sum(float(r.get("premium") or 0)
             for r in rows if r.get("contract_type") == "put")
    cp = sum(float(r.get("premium") or 0)
             for r in rows if r.get("contract_type") == "call")
    return pp, cp


def should_lse(classn, underlying, phase):
    """The time + instrument gate. Returns True if this message warrants an
    LSE call under the user's rule.

    D113 (2026-11-19): 'unclear' is NOT narrative. An unclear message (strike +
    ticker/premium but no clean verb — the 'Sold X collected $Y' shape) still
    gets the LSE flow check, because the tape is exactly the thing that
    resolves an unclear short-vs-long read. Only true narrative/exit/link/empty
    skip LSE."""
    if classn in ("exit", "hold", "narrative", "link", "empty"):
        return False
    if classn not in ("entry", "unclear"):
        return False
    if phase != "rth":                       # off-hours / pre / post / closed
        return True                          # any underlying, freely
    # market hours: SPX only
    try:
        import ticker_taxonomy as tx
        return tx.canonical_of(underlying) == "SPX"
    except Exception:
        return str(underlying or "").upper().startswith("SPX")


def tag(msg_id, raw, underlying="", phase="closed", log=print):
    """Tag one message. UPSERT by msg_id: the ARRIVAL call classifies it with no
    instrument yet; the POST-PARSE call passes the resolved underlying and
    upgrades the same row with the flow verdict. One row per message, never two
    — and the flow call only ever fires on the pass that knows the instrument."""
    msg_id = str(msg_id)
    classn = classify(raw)
    do_lse = should_lse(classn, underlying, phase)
    flow_verdict, flow_check = "", ""
    if do_lse and underlying:
        try:
            pp, cp = lse_flow(underlying)
            if pp is None:
                flow_check = "unavailable"
            else:
                flow_check = "yes"
                total = pp + cp
                pshare = f"{100 * pp / total:.0f}%" if total else "n/a"
                flow_verdict = (f"put ${pp:,.0f} vs call ${cp:,.0f} "
                                f"({pshare} put)")
        except Exception as e:
            flow_check = f"failed:{type(e).__name__}"
    action = {
        "entry": "trade or park (run gates)",
        "unclear": "ambiguous intent — LSE flow + prior-shape lookup decide",
        "exit": "exit the held position",
        "hold": "no action (management)",
        "narrative": "no action",
        "link": "skip",
        "empty": "skip",
    }.get(classn, "no action")
    # D113: prior-shape lookup — has this shape bitten us before? Informs, never
    # blocks. Runs off-thread, its result rides the tag row as the shape note.
    shape_note = ""
    try:
        import prior_shape as _ps
        _r = _ps.lookup(raw, underlying or "")
        shape_note = _r.get("verdict", "")
    except Exception:
        pass
    row = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "msg_id": msg_id,
           "underlying": underlying or "", "classification": classn,
           "flow_check": flow_check, "flow_verdict": flow_verdict,
           "suggested_action": action, "raw": (raw or "")[:120]}

    existing, rows = None, []
    out = _out_path()
    if out.exists():
        try:
            rows = list(csv.DictReader(out.open(encoding="utf-8",
                                                errors="replace")))
            for r in rows:
                if r.get("msg_id") == msg_id:
                    existing = r
                    break
        except Exception:
            rows = []
    if existing is not None:
        # keep the arrival timestamp and any verdict already earned
        if existing.get("flow_verdict") and not row["flow_verdict"]:
            row["flow_verdict"] = existing["flow_verdict"]
            row["flow_check"] = existing.get("flow_check") or ""
        if existing.get("underlying") and not row["underlying"]:
            row["underlying"] = existing["underlying"]
        row["ts"] = existing.get("ts") or row["ts"]
        if all(row[k] == existing.get(k) for k in COLS):
            return row                     # nothing new; do not rewrite
        for i, r in enumerate(rows):
            if r.get("msg_id") == msg_id:
                rows[i] = row
                break
        with out.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLS)
            w.writeheader()
            w.writerows(rows)
    else:
        # new message: append (header first time)
        new = not out.exists()
        with out.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLS)
            if new:
                w.writeheader()
            w.writerow(row)
    tag = f"{classn}"
    if flow_verdict:
        tag += f" | LSE: {flow_verdict}"
    if shape_note and shape_note != "no prior shape — first sighting":
        tag += f" | SHAPE: {shape_note[:90]}"
    log(f"tag msg {msg_id}: [{tag}] -> {action}")
    return row


def backfill(phase="closed", limit=100, log=print):
    import csv as _csv
    ledger = HERE / "signals.csv"
    rows = list(_csv.DictReader(ledger.open(encoding="utf-8", errors="replace")))
    done = set()
    if OUT.exists():
        done = {r["msg_id"] for r in _csv.DictReader(OUT.open(encoding="utf-8"))}
    n = 0
    for r in reversed(rows):
        mid = r.get("msg_id")
        raw = r.get("raw") or ""
        if not mid or mid in done or not raw.strip():
            continue
        tag(mid, raw, r.get("instrument") or "", phase, log=log)
        n += 1
        if n >= limit:
            break
    log(f"backfilled {n} message(s)")
    return n


def report():
    if not OUT.exists():
        print("no message_tags.csv — run: python message_tag.py --backfill")
        return 0
    rows = list(csv.DictReader(OUT.open(encoding="utf-8")))
    print(f"=== MESSAGE TAGS ({len(rows)} tagged) ===")
    for r in rows[-40:]:
        extra = f" | LSE: {r['flow_verdict']}" if r["flow_verdict"] else ""
        print(f"  {r['msg_id']:<6} {r['classification']:<10} {r['underlying']:<8}"
              f"{extra[:60]}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        return report()
    if a.backfill:
        import james_pipe as jp
        backfill(jp.session_phase(), a.limit)
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
