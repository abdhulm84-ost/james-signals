🛑 WHY THE BIG WIN NEVER FILLED — root cause + two fixes (audit of 4524/4528)

YES, those two scorecard wins (SPXW +25%, QQQ 648P +7%) are JAMES's results on his numbers. What WE did on the same messages:

• SPXW debit spread — NOT traded: both buy orders (13.50 image / 13.80 text) expired unfilled, $0. Tradier's own records: avg_fill 0.0, expired 22:16 UTC.
• QQQ 648P — traded: filled 2.67 (better than his 2.53) but held to expiry vs QQQ 645.04 → −$29. His "bought back at $235" came when two QQQ positions were open — refusing to guess which one was correct.
• QQQ 650/648 image ticket — traded: stopped out → −$7. Batch net: −$36.

THE REAL REASON THE SPXW DIDN'T CAPITALISE — his signal was a stale paste:
• He posted at 17:24 UTC with MORNING screenshots (13.80 → 16.10 already banked, "2nd target done" minutes later).
• The real tape at 17:44 UTC: SPX 7,242.95 → the 7,200/7,220 spread's own floor was 20.00. His "$13.8" could no longer exist.
• Our orders went out in 49 seconds (latency was fine) — they just sat 6+ points under a market that had already run, then expired. The text typo ("Sell 70C") even anchored into a 7200/7270 70-wide order at 13.8 — floor 42.
• Had they filled on the late-day collapse (SPX closed 7,175.89, spread → 0) it would have been a falling-knife loss. Not filling was fortune; refusing was not yet designed.

TWO FIXES BUILT (guards green, suite running):
• D147 — stale-entry floor gate: at placement, if the spread's spot-derived intrinsic floor sits >15% ABOVE his stated entry, the signal is parked on arrival with a 🛑 STALE ENTRY push — the order never rests. Credit packages stay exempt (a sell limit fills better on a rally), the vision-repair path (3956) still trades, no spot quote → never blocks.
• D148 — the exit fast gate no longer counts unfilled entries as holdings: a book of {unfilled, filled} reads as ONE instrument, so bare exits bind instead of refusing.

STILL QUEUED: D144 ("Nth target done" closes an open position) — needs your go.
Net: next time a post-move paste arrives, you get an instant "stale, not entered" alert instead of a 5-hour zombie order.
