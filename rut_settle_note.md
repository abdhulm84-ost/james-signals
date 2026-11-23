🧩 RUT PARK WIRING — D142 live (2026-11-21): 4540 settled with a REAL print

The RUT park (msg 4540) was stuck unsettled. Two stacked holes, both fixed:

1. NO official close existed anywhere in our chain — probed live: Tradier's history serves SPX (11-19: 7,175.83 · 11-20: 6,910.39) but returns EMPTY for RUT; BlackBull's RUT print is the D133 garbage (an SPX-level number).
2. The settle cache stored the first failure FOREVER — the documented "print not up yet, retry later" could never actually retry. Fixed: a cached None now re-fetches.

THE FIX (better than the proxy you approved): probing showed LSE's tape carries REAL RUT prints for Friday — true close 2,581.79, cross-validated by IWM 256.61 x 10.06. The proposed IWM x10.2 proxy would have been 35 points off (real ratio ~10.06) — enough to flip a verdict near a strike. So RUT now settles from the real tape, not a guessed ratio:
• verdict tagged "(LSE tape close)" — provenance in the register, never silent
• capped at 5 attempts/day — a missing date can never become a call storm
• past 3 days with nothing anywhere → still honest "unsettleable", no fiction

SETTLED (written while the pipe was stopped, D115):
• 4540 RUT 2665/2655P ($440 credit) → settle 2,581.79 → would have lost -$560 (MAX LOSS) → "would have lost (LSE tape close)". The thin-cushion park saved the full $560 — now verified against the real tape, not estimated via IWM.

PROOF: test_settle_rut.py 9/9 + full suite 0 FAILs + live end-to-end on the real row. DEFECT_REGISTER now D130-D142. Pipe restarted LIVE, preflight OK.

Also scored since last night: SIM4543 shadow settled -$560 "ack was right" (SPX official close 6,910.39 — not taking the 4543 duplicate avoided it; our live TL4544 exited early for only -$50), and park 4530 saved $116 (QQQ settled 608.45).
