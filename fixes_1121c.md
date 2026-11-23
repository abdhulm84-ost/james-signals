⚓ REAL-SPOT ANCHOR + GRADED GATE — D140/D141 live (2026-11-21), the "fix it now" build

The fix for "why did our sandbox record a loss" — two halves:

D140 — closes now have a price anchor OUTSIDE our own feed:
• Every credit-spread close takes limit = min(our mark band, REAL-SPOT BOUND). The bound = sold-leg intrinsic + time-value allowance, floored at the width — pure geometry in james_core, one definition.
• Spot comes from the venue's real print (BlackBull index level / real stock quote), sanity-banded against the strikes — BlackBull's RUT-print-returns-SPX garbage (the D133 case) yields NO anchor and the mark band stands. An anchor never blocks an exit on data it can't prove.
• Effect: if our own mark is skewed (TL4535: we marked 9.0, his broker showed 5.15), the close can no longer fill anywhere it likes — only at or below what the REAL market implies the package is worth. Full closes AND partial sub-side closes covered.

D141 — the graduation gate now reads the real tape:
• The LSE audit grades SPREAD fills against the tape's real underlying price: fill above the bound = ANCHOR_BREACH (Friday's 30.40-on-a-15-wide @ spot 7007 is the archetype), not soft CONTEXT.
• The SPX milestone (>=62% on 30 clean) EXCLUDES any trade the audit graded DIVERGED/ANCHOR_BREACH — a paper win the OPRA prints refute can no longer push the gate to PASS. Exclusion printed on the milestone line: the sample shrinks by an unknown, it never gains a fiction.

What can't be fixed: paper fills will never equal his fills or live NBBO — that's the venue. This build removes its power over our numbers; micro-live remains the final validator per the ladder.

Proof: test_d137_exit_speed (D140 geometry + wiring) and test_lse_fill_audit (D141 breach + gate fixture) both ALL PASS; DEFECT_REGISTER now D130-D141. Full suite running before the restart.
