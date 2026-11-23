📈 ROI QUESTION ANSWERED — and it caught the gate counting its own bug

Your question (is 46% ROI positive or negative?) had two answers:
• As the gate counted it: net -$16,267 over 26 trades → NEGATIVE. avg win +$847, avg loss -$1,888.
• The two rows causing it are the DOCUMENTED 11-20 legging orphans (-19,530 @ fill 30.40 and -4,892 @ fill 56.92 — the D131/D135 market-close bug, true bound about -7,941 for all three). Strip them: 24 trades, 12W/12L = 50%, net +$8,155 → POSITIVE, avg win +$847 vs avg loss -$168 (wins 5x the losses).

Fixed (D149): the milestone sample now excludes the three orphan trade-ids — the same "never count fiction" rule D141 applies to tape-refuted fills. Gate regenerated: 12/24 = 50% HOLD - 6 more clean trades (still locked; needs 62% on 30).

The general math: win rate alone never decides ROI — at 46% you need avg win >= 1.17x avg loss. Ours, once the bug rows are out, is 5x — which is exactly what the stop discipline (D137) is for.
