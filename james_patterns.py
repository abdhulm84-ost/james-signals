#!/usr/bin/env python3
"""james_patterns.py — James's footprint, bookmarked (user directive 2026-11-18).

Every signal is fingerprinted by STYLE (not content), recorded with its outcome,
and matched against history when a similar one arrives. The match is ADVISORY:
it prints context next to the trade and never changes a decision. The bar for
letting a pattern drive size stays 30 clean outcomes, same as the golden band.

Fingerprint dimensions
  structure      vertical_credit | iron_condor | single_option | iron_butterfly
  direction      bullish | bearish | neutral
  dte            0DTE | 1-2 | 3+
  quality        credit/width bucket (fat >=0.55 | thin <0.55 | n/a)
  mgmt           did he add / trim during the life (add | trim | hold)

  python james_patterns.py --build      # rebuild the ledger from history
  python james_patterns.py --for <msg>  # advisory line for a new signal
  python james_patterns.py --report     # the pattern table
"""
import argparse
import csv
import datetime
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

OUT = HERE / "james_patterns.csv"
COLS = ["fp", "instrument", "structure", "direction", "dte", "quality", "mgmt",
        "n", "wins", "losses", "net", "last_seen", "example_msg"]


# ---------------------------------------------------------------- fingerprint
def dte_bucket(expiry, sent_day):
    try:
        d1 = datetime.date.fromisoformat(str(expiry)[:10])
        d0 = datetime.date.fromisoformat(str(sent_day)[:10])
        d = (d1 - d0).days
    except (TypeError, ValueError):
        return "?"
    return "0DTE" if d <= 0 else ("1-2" if d <= 2 else "3+")


def quality(credit, width):
    try:
        c, w = float(credit), float(width)
        if not w:
            return "n/a"
        return "fat" if c / w >= 0.55 else "thin"
    except (TypeError, ValueError):
        return "n/a"


def fingerprint(structure, direction, dte, qual, mgmt="hold", instrument="?"):
    """The BAND identity (2026-11-18, user: "segregation will act as each band").
    Instrument is part of the band: an SPX bear call and a QQQ bear call are
    different edges, and pooling them hides the one that works. The canonical
    ticker comes from ticker_taxonomy so SPX/SPXW/SPX500 are ONE band."""
    try:
        import ticker_taxonomy as _tx
        inst = _tx.canonical_of(instrument) or "?"
    except Exception:
        inst = (str(instrument or "?").split()[0] or "?").upper()
    return f"{inst}|{structure}|{direction}|{dte}|{qual}|{mgmt}"


# ------------------------------------------------------------------- loading
def load_settled():
    """Settled outcomes with the style fields we need. Source: park_outcomes
    (counterfactual) + closed trades (realised)."""
    rows = []
    po = HERE / "park_outcomes.csv"
    if po.exists():
        for r in csv.DictReader(po.open(encoding="utf-8")):
            v = (r.get("verdict") or "")
            if not (v.startswith("WOULD") or v.startswith("would have")):
                continue
            rows.append({
                "msg_id": r.get("msg_id"), "structure": r.get("structure"),
                "direction": r.get("direction"),
                "expiry": r.get("expiry"), "park_ts": r.get("park_ts") or r.get("ts"),
                "credit": r.get("credit"), "width": r.get("width"),
                "instrument": r.get("underlying"),
                "pnl": r.get("would_pnl"), "source": "park"})
    st = HERE / "state.json"
    if st.exists():
        try:
            data = json.loads(st.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        for tid, t in (data.get("closed") or {}).items():
            pnl = t.get("pnl")
            if pnl is None:
                continue
            legs = t.get("legs") or []
            if len(legs) >= 2:
                ks = [float(l[0]) for l in legs if l and l[0] is not None]
                width = round(max(ks) - min(ks), 2) if len(ks) >= 2 else ""
                struct = "vertical_credit" if t.get("direction") == "short" else "vertical_long"
            else:
                width, struct = "", "single_option"
            rows.append({
                "msg_id": t.get("msg_id"), "structure": struct,
                "direction": ("bullish" if t.get("direction") == "short" else "bearish"),
                "expiry": t.get("expiry"), "park_ts": (t.get("opened") or "")[:10],
                "credit": t.get("entry"), "width": width,
                "instrument": t.get("instrument"),
                "pnl": pnl, "source": "trade"})
    return rows


# -------------------------------------------------------------------- build
def build():
    agg = {}
    for r in load_settled():
        dte = dte_bucket(r.get("expiry"), r.get("park_ts"))
        q = quality(r.get("credit"), r.get("width"))
        _inst = r.get("instrument") or "?"
        fp = fingerprint(r.get("structure") or "?", r.get("direction") or "?",
                         dte, q, instrument=_inst)
        a = agg.setdefault(fp, {"n": 0, "wins": 0, "losses": 0, "net": 0.0,
                                "last_seen": "", "example_msg": "",
                                "instrument": _inst,
                                "structure": r.get("structure"),
                                "direction": r.get("direction"), "dte": dte,
                                "quality": q, "mgmt": "hold"})
        a["n"] += 1
        try:
            p = float(r.get("pnl"))
        except (TypeError, ValueError):
            p = 0.0
        a["net"] += p
        if p > 0:
            a["wins"] += 1
        elif p < 0:
            a["losses"] += 1
        ts = str(r.get("park_ts") or "")
        if ts > (a["last_seen"] or ""):
            a["last_seen"] = ts
            a["example_msg"] = str(r.get("msg_id") or "")
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for fp, a in sorted(agg.items(), key=lambda kv: -kv[1]["net"]):
            w.writerow({**a, "fp": fp})
    print(f"built {len(agg)} pattern(s) from history -> {OUT.name}")
    return agg


# ------------------------------------------------------------------- lookup
def load_patterns():
    if not OUT.exists():
        return {}
    return {r["fp"]: r for r in csv.DictReader(OUT.open(encoding="utf-8"))}


def advisory(structure, direction, dte, credit, width, instrument="?"):
    """One-line context for a NEW signal. Never blocks, never sizes.

    TWO-LEVEL BAND (2026-11-18, user: "segregation will act as each band"):
    the specific instrument is the tightest band, but a thin instrument falls
    back to its CLASS band (index / etf / stock) so a fresh ticker still has
    context instead of a blank. Falls back again, never guesses.
    """
    pats = load_patterns()
    q = quality(credit, width)
    fp = fingerprint(structure or "?", direction or "?", dte, q,
                     instrument=instrument)
    p = pats.get(fp)
    if not p:
        # widen to the ticker CLASS: same structure/direction/dte/quality,
        # any instrument in the same bucket
        try:
            import ticker_taxonomy as _tx
            bucket = _tx.bucket_of(instrument)
        except Exception:
            bucket = None
        best = None
        if bucket:
            for k, cand in pats.items():
                parts = k.split("|")
                if len(parts) != 6:
                    continue
                _inst = parts[0]
                try:
                    if _tx.bucket_of(_inst) != bucket:
                        continue
                except Exception:
                    continue
                if "|".join(parts[1:]) != "|".join(
                        [structure or "?", direction or "?", dte, q, "hold"]):
                    continue
                n = int(cand.get("n") or 0)
                if best is None or n > int(best[1].get("n") or 0):
                    best = (k, cand)
        if best:
            k, cand = best
            n, w, l = int(cand.get("n") or 0), int(cand.get("wins") or 0), int(cand.get("losses") or 0)
            net = float(cand.get("net") or 0)
            dec = w + l
            wr = f"{100 * w / dec:.0f}%" if dec else "n/a"
            return (f"📌 {bucket.upper()} band {k} (closest match, not "
                    f"{instrument}): {w}W/{l}L ({wr}), net ${net:+,.0f} over "
                    f"{n} — advisory only"), fp
        return f"🆕 new band ({fp}) — no history in this class yet", fp
    n = int(p["n"] or 0)
    w, l = int(p["wins"] or 0), int(p["losses"] or 0)
    net = float(p["net"] or 0)
    dec = w + l
    wr = f"{100 * w / dec:.0f}%" if dec else "n/a"
    tag = "advisory only"
    if n < 30:
        tag += f", n={n} (<30 = not evidence)"
    return (f"📌 pattern {fp}: {w}W/{l}L ({wr}), net ${net:+,.0f} "
            f"over {n} — {tag}"), fp


def report():
    pats = load_patterns()
    if not pats:
        print("no patterns yet — run: python james_patterns.py --build")
        return 0
    print(f"=== JAMES FOOTPRINT ({len(pats)} patterns) ===")
    print(f"{'pattern':<52}{'n':>4}{'W':>4}{'L':>4}{'net':>12}")
    for fp, p in sorted(pats.items(), key=lambda kv: -float(kv[1]["net"] or 0)):
        print(f"{fp:<52}{p['n']:>4}{p['wins']:>4}{p['losses']:>4}"
              f"{float(p['net'] or 0):>+12,.0f}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--for", dest="msg", default="")
    ap.add_argument("--structure", default="")
    ap.add_argument("--direction", default="")
    ap.add_argument("--dte", default="0DTE")
    ap.add_argument("--credit", default="")
    ap.add_argument("--width", default="")
    a = ap.parse_args()
    if a.build:
        build()
        return 0
    if a.report:
        return report()
    if a.structure:
        line, fp = advisory(a.structure, a.direction, a.dte, a.credit, a.width)
        print(line)
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
