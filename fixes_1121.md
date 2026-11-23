🔧 OVERNIGHT FIXES — D133-D136 registered and LIVE (2026-11-21), full suite green

Your Q — "is the TL4535 class fixed now?" → IT IS NOW. It was flagged, not fixed, when you asked. Done while you slept:

D134 — status screenshots never open trades (TL4535, TL4522):
• Root cause: the entry guard knew 'holding' but NOT 'N% up', so '10% up' / '30% up on bull puts' counted as non-status → the position-screenshot card survived → trade opened. Both entered AFTER the move (TL4535 -100; TL4522 -50 on the exact 7315/7300 legs of TL4520).
• Fix (two layers): a bare '% up' with no action verb = status → card dies at the guard; the entry gate refuses it outright — only a real order ticket ('TO OPEN') may override. Exit side untouched ('% up, pl book gains' still exits, D121 intact). Image-only tickets still mirror. Guard: test_d134_status.py, 24/24.

D135 — spread closes can no longer pay more than their width:
• The -$32,072 orphan closes: MARKET multileg orders legged in Friday's crash — QQQ pair filled 9.31 NET on a 2-WIDE spread (legs a beat apart), SPXW 30.40 on 15-wide, 56.92 on 20-wide. All three exceed the mathematical max → -24,131 of the booked loss was legging artifact; the true defined-risk bound was exactly the estimated -7,941.
• Fix: credit-spread closes now send a DEBIT LIMIT AT WIDTH (full and partial closes). A vertical can never be worth more than its width, so every legitimate fill still crosses it — only impossible packages are blocked. Long/debit verticals unchanged (their close is a credit order).

D136 — RETRACTION: the 4527 shadow '+$1,350 missed winner' in last night's audit was WRONG:
• SIM4527 was a DEBIT spread (paid 13.5 for 7200/7220C) that expired WORTHLESS at SPX 7,175.89. The shadow passed the cost as a positive 'credit' → booked +1,350 'MISSED TRADE' when the truth is -1,350 — acking SAVED the full debit. 4527 was never a missed win: it was an avoided loss (and is now D134-blocked at the source).
• State repaired while pipes stopped: SIM4527 → pnl -1,350, 'ack was right', correction row in signals.csv. Shadows now sign debits from leg geometry (settle_parked's rule). SIM4475 (still open) will settle correctly.

D133 — RUTW spot bug: the '62.7% from spot 7,151.30' on msg 4540 — 7,151.30 was SPX's own level (BlackBull's RUT print is wrong). Index roots now refuse a spot outside [0.4x, 2.5x] of every strike → 'bad print' → the conservative park paths decide. The 4540 park was right by luck; now it's right by construction.

New since the audit — msgs 4543-4545 handled correctly:
• 4544 'Sell 7010P n Buy 7000P collect $400-420' → credit guard repaired 4.00→3.00 on chain mid, filled 3.2. 4543 (image, same structure) → deduped + shadowed (SIM4543), no duplicate. 4545 'Take gains' → TL4544 closed in 324s @ 3.70 = -50. Book open: TL4515 INTU only.

No new messages after 4545. Pipe restarted with all four fixes; Saturday = repair window safely closed.
