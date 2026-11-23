#!/usr/bin/env python3
"""park_bands.py — win/loss bands from park_outcomes.csv (the 360° cut).

Segregates the would-have outcomes into bands (direction x credit quality,
park reason, DTE, underlying) so winners and losers can be told apart BEFORE
the next signal arrives. Regenerates PARK_WIN_LOSS_REPORT.md:

  python park_bands.py            # print the bands
  python park_bands.py --md      # rewrite PARK_WIN_LOSS_REPORT.md

Excludes junk rows (blank raw, replay exit markers). Read-only.
"""
import argparse
import collections
import csv
import datetime
import re
import sys

import james_pipe as jp


def cls(v):
    return "won" if v.startswith("WOULD HAVE WON") else (
        "lost" if v.startswith("would have lost") else "other")


def clean_rows():
    """Rows fit for ENTRY band analysis: non-empty raw, not replay exit
    markers, and — per the verify-figures rule — ONE row per message (replay
    ids re-park the same message; tl-fly5-8 was one fly counted 4×) and only
    messages that READ like an entry (a hold/status narrative is not a
    would-have trade: 'we are holding Bear calls' has no sell/buy/collect)."""
    seen = set()
    out = []
    for r in csv.DictReader(jp.PARK_OUT.open(encoding="utf-8")):
        raw = (r.get("raw") or "").strip()
        if not raw or str(r.get("msg_id", "")).startswith("r-exit"):
            continue
        if not re.search(r"\b(sell|buy|collect)\b", raw, re.I):
            continue
        k = re.sub(r"\s+", " ", raw.lower())[:60]
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def rclass(r):
    t = (r.get("reason") or "").lower()
    if "itm" in t or "strike drift" in t:
        return "A. ITM/strike-drift guard"
    if "break-even" in t or "break even" in t:
        return "B. near break-even guard"
    if "impossible" in t or "1-wide" in t:
        return "C. credit implausible"
    if "contradict" in t:
        return "D. text-credit conflict"
    if "circuit" in t:
        return "E. naked circuit breaker"
    if "not listed" in t:
        return "F. strike/expiry not listed"
    if "ticker" in t or "root" in t or "unresolv" in t:
        return "G. root/symbol unresolvable"
    if "tradier" in t or "placement" in t or "reject" in t:
        return "H. venue placement failed"
    if "expiry" in t:
        return "I. expiry guard"
    if "credit spread" in t or "labelled" in t or "structure" in t:
        return "J. structure/label guard"
    return "K. other"


def quality(r):
    try:
        c, w = float(r["credit"]), float(r["width"])
    except (TypeError, ValueError):
        return "n/a"
    if not w:
        return "n/a"
    return "hi>=.55" if c / w >= 0.55 else "lo<.55"


def dte(r):
    try:
        p = datetime.date.fromisoformat((r.get("expiry") or "")[:10])
        s = datetime.date.fromisoformat((r.get("park_ts") or r.get("ts") or "")[:10])
        d = (p - s).days
        return "0DTE/today" if d <= 0 else ("1-2 dte" if d <= 2 else "3+ dte")
    except (TypeError, ValueError):
        return "?"


def band(rows, keyfn):
    b = collections.defaultdict(lambda: [0, 0, 0, 0.0])
    for r in rows:
        t = b[keyfn(r)]
        t[0] += 1
        if cls(r["verdict"]) == "won":
            t[1] += 1
        elif cls(r["verdict"]) == "lost":
            t[2] += 1
        try:
            t[3] += float(r["would_pnl"])
        except (TypeError, ValueError):
            pass
    return b


def fmt(b):
    out = []
    for k, (n, w, l, net) in sorted(b.items(), key=lambda kv: -kv[1][3]):
        dec = w + l
        wr = f"{100 * w / dec:.0f}%" if dec else "n/a"
        out.append(f"  {k:<36} {n:>3}  {w}W/{l}L  {wr:>4}  net ${net:>+9,.0f}")
    return "\n".join(out)


def sections(dec):
    return [
        ("BAND: direction x credit-quality (the segregation)", lambda r: f"{r['direction']:<8} q={quality(r)}"),
        ("park reason class", rclass),
        ("reason x direction", lambda r: f"{rclass(r)[:16]} {r['direction']}"),
        ("DTE at park", dte),
        ("underlying", lambda r: r.get("underlying") or "(none)"),
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", action="store_true", help="rewrite PARK_WIN_LOSS_REPORT.md")
    a = ap.parse_args()
    rows = clean_rows()
    dec = [r for r in rows if cls(r["verdict"]) in ("won", "lost")]
    won = [r for r in dec if cls(r["verdict"]) == "won"]
    lost = [r for r in dec if cls(r["verdict"]) == "lost"]
    net = sum(float(r["would_pnl"]) for r in dec if r["would_pnl"] not in ("",))
    gw = sum(float(r["would_pnl"]) for r in won if r["would_pnl"] not in ("",))
    gl = sum(float(r["would_pnl"]) for r in lost if r["would_pnl"] not in ("",))

    lines = [f"clean rows {len(rows)}, decided {len(dec)}: "
             f"{len(won)}W/{len(lost)}L = {100 * len(won) / max(1, len(dec)):.1f}%  net ${net:+,.0f}",
             f"avg win ${gw / max(1, len(won)):+,.0f}  avg loss ${gl / max(1, len(lost)):+,.0f}"]
    for title, fn in sections(dec):
        lines.append(f"--- {title} ---\n" + fmt(band(dec, fn)))
    byday = collections.defaultdict(list)
    for r in sorted(dec, key=lambda r: r.get("ts") or ""):
        byday[(r.get("expiry") or "?")[:10]].append("W" if cls(r["verdict"]) == "won" else "L")
    lines.append("--- streak map (by expiry date) ---")
    for d, seq in sorted(byday.items()):
        lines.append(f"  {d}: {''.join(seq)}")
    text = "\n".join(lines)
    print(text)
    if a.md:
        md = _wrap_md(text, rows, dec, won, lost, net)
        (jp.HERE / "PARK_WIN_LOSS_REPORT.md").write_text(md, encoding="utf-8")
        print("\nWROTE PARK_WIN_LOSS_REPORT.md")
    return 0


def _wrap_md(text, rows, dec, won, lost, net):
    return f"""# Parked-trade win/loss bands (regenerated {datetime.date.today().isoformat()})

Every PARKED message is settled at its expiry close in `park_outcomes.csv`
(per-lot, would-have economics). This report segregates winners from losers so
the gate can be tuned on evidence, not vibes. Regenerate: `python park_bands.py --md`.

Numbers below are COUNTERFACTUALS of trades the gates refused: a win here is
money the pipeline did NOT take; a loss here is money the gates SAVED.

## Headline
- clean rows {len(rows)}, decided {len(dec)}: **{len(won)}W / {len(lost)}L = {100 * len(won) / max(1, len(dec)):.1f}%**, net **${net:+,.0f}/lot**
- gross missed wins ${sum(float(r['would_pnl']) for r in won if r['would_pnl'] not in ('',)):+,.0f} vs saved losses ${sum(float(r['would_pnl']) for r in lost if r['would_pnl'] not in ('',)):+,.0f}

## Bands (win vs loss segregation)

```
{text}
```

## The segregation rule (evidence-derived, deduped figures)

**USER POLICY 2026-11-13 — poison bands are WATCH-ONLY:** once a structure is
identified as a losing band (bullish thin-credit, neutral flies/condors), it is
NOT traded in the main lane — it is watched and its outcome captured (park +
settle), nothing more. It never graduates to real funds.

Counts below are ONE ROW PER MESSAGE (replay ids re-park the same message —
tl-fly was one fly counted 4x) and entry-like messages only (hold/status
narratives are not would-have trades).

| band | record | verdict |
|---|---|---|
| **BEARISH + thin credit (<0.55 of width)** | 19W/0L, +$6,980 | THE golden band — every one won |
| bearish + fat credit (>=0.55) | 2W/3L, -$503 | losing after dedupe — keep gated |
| bullish + fat credit | 1W/1L, -$300 | tiny sample — park + capture |
| **bullish + thin credit** | 6W/6L, -$1,546 | coin flip OVERALL — but the cycle splits it (below) |
| neutral (flies/condors) | 1W/2L, +$119 | tiny n after dedupe — park + capture |

**The bullish-thin CYCLE (the winning-cycle refinement):**
- 0DTE ("for today") bull puts: **4W/1L, +$676** — the winning cycle
- dated (1+ day) bull puts: **2W/5L, -$2,222** — the knife (sold into the slide)
If this band is ever re-enabled, ONLY the same-day form on a green session
qualifies; the dated form is what gave the band its poison name.

**Why the credit/width inversion**: >=0.55 is NOT quality here, it is ITM
PROXIMITY — the market pays fat premium because the short strike is near the
money. In the TRADED book the >=0.55 bucket won only because the ITM/near-BE
guards filtered the dangerous ones pre-entry; the park book shows the refused
ones raw. The real discriminator is **direction x OTM-safety**, not the ratio.

**Why losses are bigger than wins**: credit-spread geometry — wins cap at the
credit collected, losses run to width-credit. Structure, not a bug: the hit
rate must stay above ~57% for expectancy. The DATED bull puts are what drag the
average loss up; the same-day sub-cycle does not.

**Why bearish won**: the window (10-28 to 11-09) carried a 90-pt SPX slide;
James's bearish defined-risk calls caught it. NOT a law of nature — the user's
own rule: bearish is tradable WHEN THE ANALYSIS IS STRONG (predictions/levels
aligned), because the market can flip.

## Fix targets (missed-money value order, deduped)

1. **J. structure/label guard (+$3,042, 15W/5L)**: parks where the BRAIN
   mislabeled a structure whose TEXT legs parse cleanly. The ticket beats the
   text (D35) but the brain label must NOT beat the text. SHIPPED: current
   text-first code replays these messages to PLACED (verified 2026-11-13).
2. **H. venue placement failed (+$1,105, 4W/3L)**: caution — much of this band
   is REPLAY artifacts (D45: expiry year-wrap to 2027 on reprocessed messages).
   True live venue misses are a smaller number; placement rescue is parked
   until the prod-account probe.
3. **A. ITM guard (bearish 4W/0L +$1,012 vs bullish 2W/4L -$1,709)**: SHIPPED
   as D44 — bearish defined-risk hits shrink to 1 lot instead of parking; the
   bullish side keeps the park (watch-only).

## Graduation data (for the SPX real-funds bot)

Streak map above is per expiry date. The golden band (bearish thin-credit) is
on a 19-win in-sample streak after dedupe. Graduation rule for real funds:
per-band rolling window (last 30 clean outcomes): win rate >= 62% AND current
streak >= 3 -> the band graduates paper -> 1 lot real -> scale +1 lot per
additional 5 wins. PAPER stays the control arm; demote on two consecutive
losses past the gate. See SPX_BOT_PLAN.md.
"""


if __name__ == "__main__":
    sys.exit(main())
