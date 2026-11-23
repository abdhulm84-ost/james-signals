📊 LSE QUOTA CHECK + FILL-AUDIT BUILT (2026-11-21) — your question answered with the server's own numbers

QUOTA: nowhere near exhaustion. From /v3/usage on our key: free plan — 60 calls/min (client auto-waits 429s, a bug can't hammer past it), 12.5M rows/day (0 used), 12.89 GB/week (0 used). Our real burn: 1-6 calls/day under your own gate (off-hours free / RTH SPX-only / never on narrative) + ~1 per fill-regate. Roughly 1,000x headroom — the gate prevents waste, not the ceiling.

DISCOVERY while testing: LSE's tape lags ~1 day — newest print today is 11-19, Friday's fills are not fetchable yet (data load, not quota, is the pacing constraint). So over-calling is structurally impossible; your "SPX + golden band first" instinct is exactly how to spend what we do use.

BUILT: lse_fill_audit.py — grades our fills against the real tape, next-day:
1. SPX/index-family sorted FIRST (graduation-gate fills graded before anything else)
2. ARCHIVE-FIRST — lse_flow.csv scores index fills with zero live calls
3. HARD BUDGET 8 live calls/day (json counter; exhausted -> BUDGET_SKIPPED, never silent)
4. CFD fills never spend a token; fetches are window-scoped +/-10min (the day-level "newest 5000" truncation missed mid-day fills on run one)
5. Verdicts: single options -> GOOD/DIVERGED at the 25% band vs the exact OCC print; spreads -> CONTEXT (a per-print tape has no package price); NO_TAPE + a lag hint when the data simply isn't loaded yet.

Proof: 20/20 guard tests. First live run scored all 11 of Friday's fills and exposed the lag (budget 7/8 today). Next real grading = Monday run with a fresh budget once the tape covers Friday — say the word and I'll schedule it.
