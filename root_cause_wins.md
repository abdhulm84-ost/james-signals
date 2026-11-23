🧩 ROOT CAUSE — every his-win vs our-result, mechanism by mechanism

1) SPXW 7200/7220C — his +25%, ours $0 (unfilled)
   Cause: STALE PASTE — his 17:24 message carried morning prices while SPX sat 7,242.95 (floor of the spread = 20.00 vs his 13.8). Orders rested under market, expired.
   Status: FIXED — D147 refuses such entries on arrival with the numbers; before that the order just zombied.

2) QQQ 648P — his +7%, ours -$29
   Cause: we were in a NAKED short put (banned class); his "bought back at $235" arrived when two QQQ positions were open — binding either would have been a guess.
   Status: FIXED — D113 refuses the naked class outright; the refusal itself was correct (never guess).

3) SPXW 7170/7155P — his +10% (later claimed 30%), ours -$100
   Cause: we ENTERED OFF A STATUS SCREENSHOT ("10% up") at 8.00 vs his 6.60 credit, exited at 9.00 on the next status. Pure pre-D134 defect.
   Status: FIXED — D134: "% up" can score a row, never open a trade. That message now produces the +10% line, not an order.

4) SPXW 7175/7165P — his +10%, ours never held
   Cause: NO ENTRY SIGNAL WAS EVER POSTED — only position screenshots (first seen msg 4532). We do not invent entries from screenshots of his book.
   Status: NOT A DEFECT — uncapturable by design. His win here is simply outside our feed.

5) SPXW 7010/7000P — his reported +48%, ours -$50  ← decomposed
   Entry gap 1.20/share: he sold at 4.40 (his fill, then posted); we sold at 3.20 when his message arrived — chain-mid said fair was 3.00, so OUR fill was good, his was EARLIER. Cause: fill->post lag (his workflow: fill first, tell us later). STRUCTURAL — cannot beat his posting order; we can only act fast on what's still fresh (49s measured) and refuse what's impossible (D147).
   Exit gap 1.40/share is MY SCORING ERROR: the +48% came from his screenshot mark 2.30 — but the phone clock on that screenshot runs ~30 min behind his message (12:25 vs the 18:55 UTC post; the same ~30 min offset appears on four other screenshots). Our own fill proves the market at his call: we exited at 3.70, THREE minutes after his "Take gains". His real fill was ~3.6-3.7, so his real return is ~+17%, not +48%.
   Status: row keeps HIS posted number with a mark-not-fill note; the proof above is the correction of record. Remaining REAL gap = the 1.20 entry lag (structural).

6) RUT 2665/2655P — his "1st target done" (no price), ours $0 parked
   Cause: thin cushion parked it; tape later proved a $560 save had we traded. His outcome unknowable — no exit level posted (if held to the 2,581.79 settle it was max loss).
   Status: WE WERE AHEAD HERE — the park rule, verified.

THE FOUR BUCKETS behind every transfer gap:
  A. fill->post lag (structural) — his workflow; mitigated by 49s latency + fresh-only + D147
  B. status/stale entries (defect) — D134/D147: FIXED
  C. defect classes naked/legging/measurement — D113/D135/D140/D141/D149: FIXED
  D. signals never posted — uncapturable by design; scorecard records only what exists

And one self-caught: screenshot marks are STALE relative to his own messages (~30-70 min) — they price his ENTRY averages fine, but any exit read from a screenshot gets the mark-not-fill caveat and is cross-checked against our own fills when we have one.
