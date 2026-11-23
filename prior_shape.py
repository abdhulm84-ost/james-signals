#!/usr/bin/env python3
"""prior_shape.py — "have we seen this shape before?" lookup (2026-11-19).

The user's rule (2026-11-19): when a message arrives, before we trust the
parse, check whether its structural shape has bitten us before — in the defect
register, the ledger, or the park outcomes — and surface what happened. Never
re-learn the same lesson per message.

What it returns: a dict with
  * shape        — the signal_intent fingerprint (verbs/ticker/spread/direction)
  * defects      — matching DEFECT_REGISTER rows whose trigger matches the shape
  * band         — the pattern band advisory (instrument+structure+direction)
  * prior_outcome— the last ledger/park outcome for this instrument+structure
  * verdict      — one line a human (or the parser) can act on

Advisory only: never blocks, never trades, never changes a decision on its
own. It INFORMs the decision and the arrival tag.
"""
import csv
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent

REGISTER = HERE / "DEFECT_REGISTER.md"
LEDGER = HERE / "signals.csv"
PARKS = HERE / "park_outcomes.csv"


def _load_register():
    if not REGISTER.exists():
        return []
    rows = []
    text = REGISTER.read_text(encoding="utf-8", errors="replace")
    # each row is a | D## | trigger | problem | impact | fix | test | line
    for line in text.splitlines():
        if not line.startswith("| D"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) >= 3:
            rows.append({"id": cells[0], "trigger": cells[1], "problem": cells[2],
                         "impact": cells[3] if len(cells) > 3 else "",
                         "fix": cells[4] if len(cells) > 4 else "",
                         "test": cells[5] if len(cells) > 5 else ""})
    return rows


def _defect_keywords(d):
    """A lowercase searchable bag of the defect's trigger + problem + fix."""
    return " ".join([d.get("trigger", ""), d.get("problem", ""),
                     d.get("impact", ""), d.get("fix", "")]).lower()


def match_defects(shape, text):
    """Which defect-register rows describe this message's shape? Score each
    defect by how many of its keywords match the raw text or the shape."""
    t = (text or "").lower()
    bag = " ".join(str(v) for v in shape.values()).lower()
    out = []
    for d in _load_register():
        kw = _defect_keywords(d)
        if not kw:
            continue
        # score: does the raw text or the fingerprint carry this defect's verbs?
        score = 0
        # verb terms (the D110/D112 class) match when the shape carries them
        for verb in ("sold", "collected", "collecting", "naked", "past tense",
                     "sell", "bought", "paid", "gerund", "misspell"):
            if verb in kw and verb in t:
                score += 2
            elif verb in kw and verb in bag:
                score += 1
        # structure terms
        for term in ("bull put", "bear call", "spread", "vertical", "naked",
                     "0dte", "debit", "credit", "strike", "premium", "phantom",
                     "not found", "expiry", "direction", "long", "short"):
            if term in kw and term in t:
                score += 1
        if score >= 2:
            out.append((score, d["id"], d["trigger"][:70], d["fix"][:80]))
    out.sort(key=lambda x: -x[0])
    return [{"score": s, "id": i, "trigger": tr, "fix": fx}
            for s, i, tr, fx in out][:4]


def prior_outcome(instrument, structure, direction):
    """Last settled outcome for this instrument+structure+direction from the
    ledger and park outcomes. Returns None if never seen."""
    best = None
    for f, keyfn in ((LEDGER, lambda r: (r.get("instrument") or "").upper()),
                     (PARKS, lambda r: (r.get("underlying") or "").upper())):
        if not f.exists():
            continue
        try:
            rows = list(csv.DictReader(f.open(encoding="utf-8", errors="replace")))
        except Exception:
            continue
        for r in rows:
            inst = keyfn(r)
            canon = _canon(inst)
            if canon != _canon(instrument):
                continue
            pnl = r.get("pnl") or r.get("would_pnl") or ""
            verdict = r.get("verdict") or r.get("status") or r.get("result") or ""
            if pnl in ("", None):
                continue
            try:
                p = float(pnl)
            except (TypeError, ValueError):
                continue
            # prefer the most recent, and prefer a real pnl over a zero
            ts = r.get("ts") or r.get("park_ts") or ""
            key = (ts, p)
            if best is None or key > best[0]:
                best = (key, p, verdict, inst)
    if best is None:
        return None
    _, p, verdict, inst = best
    return {"pnl": p, "verdict": verdict, "instrument": inst}


def _canon(u):
    u = str(u or "").upper().strip()
    for k in ("SPXW", "SPX500", "SPX"):
        if u.startswith(k):
            return "SPX"
    if u.startswith("NDX"):
        return "NDX"
    if u.startswith("RUT"):
        return "RUT"
    return u.split()[0] if u else ""


def band_advisory(instrument, structure, direction):
    """The pattern-band context, if the band table knows this shape."""
    try:
        import james_patterns as jp
        canon = _canon(instrument)
        adv = jp.advisory(canon, structure, direction, dte=None,
                          quality=None, mgmt=None)
        return adv if adv else None
    except Exception:
        return None


def lookup(text, instrument=""):
    """One call: shape + defects + prior outcome + band. Returns a dict."""
    try:
        import signal_intent as si
    except Exception:
        si = None
    shape = si.fingerprint(text) if si else {}
    inst = instrument or _ticker_from_text(text)
    out = {
        "shape": shape,
        "defects": match_defects(shape, text),
        "prior": prior_outcome(inst, shape.get("direction") or "", ""),
        "band": band_advisory(inst, "", shape.get("direction") or ""),
    }
    parts = []
    for d in out["defects"]:
        parts.append(f"⚠️ {d['id']}: {d['trigger']} — fix: {d['fix']}")
    if out["prior"] is not None:
        p = out["prior"]
        parts.append(f"📜 prior {p['instrument']}: pnl {p['pnl']:+.2f} ({p['verdict']})")
    if out["band"]:
        parts.append(f"🎸 band: {out['band']}")
    out["verdict"] = " | ".join(parts) if parts else "no prior shape — first sighting"
    return out


def _ticker_from_text(text):
    m = re.search(r"\b[A-Z]{2,5}\b", text or "")
    return m.group(0).upper() if m else ""


if __name__ == "__main__":
    import sys
    sample = sys.argv[1] if len(sys.argv) > 1 else \
        "Sold QQQ 657P for today and collected $265"
    r = lookup(sample)
    print("shape:", r["shape"])
    print("defects:", len(r["defects"]))
    for d in r["defects"]:
        print(f"  {d['id']}: {d['trigger']}")
    print("prior:", r["prior"])
    print("band:", r["band"])
    print("verdict:", r["verdict"])
