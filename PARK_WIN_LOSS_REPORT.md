# Parked-trade win/loss bands (regenerated 2026-11-13)

Every PARKED message is settled at its expiry close in `park_outcomes.csv`
(per-lot, would-have economics). This report segregates winners from losers so
the gate can be tuned on evidence, not vibes. Regenerate: `python park_bands.py --md`.

Numbers below are COUNTERFACTUALS of trades the gates refused: a win here is
money the pipeline did NOT take; a loss here is money the gates SAVED.

## Headline
- clean rows 58, decided 44: **29W / 15L = 65.9%**, net **$+3,570/lot**
- gross missed wins $+9,535 vs saved losses $-5,965

## Bands (win vs loss segregation)

```
clean rows 58, decided 44: 29W/15L = 65.9%  net $+3,570
avg win $+329  avg loss $-398
--- BAND: direction x credit-quality (the segregation) ---
  bearish  q=lo<.55                     19  19W/0L  100%  net $   +6,980
  neutral  q=hi>=.55                     3  1W/2L   33%  net $     +119
  bullish  q=hi>=.55                     2  1W/1L   50%  net $     -300
  bearish  q=hi>=.55                     5  2W/3L   40%  net $     -503
  bullish  q=n/a                         1  0W/1L    0%  net $     -540
  bearish  q=n/a                         2  0W/2L    0%  net $     -640
  bullish  q=lo<.55                     12  6W/6L   50%  net $   -1,546
--- park reason class ---
  J. structure/label guard              20  15W/5L   75%  net $   +3,042
  H. venue placement failed              7  4W/3L   57%  net $   +1,105
  I. expiry guard                        2  2W/0L  100%  net $     +900
  G. root/symbol unresolvable            1  1W/0L  100%  net $     +300
  K. other                               1  0W/1L    0%  net $     -100
  A. ITM/strike-drift guard             10  6W/4L   60%  net $     -697
  F. strike/expiry not listed            3  1W/2L   33%  net $     -980
--- reason x direction ---
  J. structure/lab bearish              14  11W/3L   79%  net $   +2,765
  H. venue placeme bearish               4  4W/0L  100%  net $   +1,800
  A. ITM/strike-dr bearish               4  4W/0L  100%  net $   +1,012
  I. expiry guard bearish                2  2W/0L  100%  net $     +900
  G. root/symbol u bullish               1  1W/0L  100%  net $     +300
  J. structure/lab neutral               1  1W/0L  100%  net $     +219
  J. structure/lab bullish               5  3W/2L   60%  net $      +58
  H. venue placeme neutral               2  0W/2L    0%  net $     -100
  K. other bearish                       1  0W/1L    0%  net $     -100
  F. strike/expiry bullish               2  1W/1L   50%  net $     -440
  F. strike/expiry bearish               1  0W/1L    0%  net $     -540
  H. venue placeme bullish               1  0W/1L    0%  net $     -595
  A. ITM/strike-dr bullish               6  2W/4L   33%  net $   -1,709
--- DTE at park ---
  1-2 dte                               18  12W/6L   67%  net $   +1,849
  0DTE/today                            18  13W/5L   72%  net $   +1,835
  3+ dte                                 8  4W/4L   50%  net $     -114
--- underlying ---
  SPX500                                18  12W/6L   67%  net $   +2,728
  QQQ                                    7  6W/1L   86%  net $     +788
  CPNG                                   1  1W/0L  100%  net $     +430
  RDDT                                   1  1W/0L  100%  net $     +285
  APP                                    1  1W/0L  100%  net $     +265
  QCOM                                   1  1W/0L  100%  net $     +258
  TXN                                    1  1W/0L  100%  net $     +227
  MCHP                                   1  1W/0L  100%  net $     +196
  EOG                                    1  1W/0L  100%  net $     +100
  SPY                                    1  0W/1L    0%  net $      -50
  IWM                                    1  0W/1L    0%  net $     -100
  GOOG                                   1  0W/1L    0%  net $     -227
  SPX                                    7  4W/3L   57%  net $     -250
  GLW                                    1  0W/1L    0%  net $     -540
  GLD                                    1  0W/1L    0%  net $     -540
--- streak map (by expiry date) ---
  2026-10-28: L
  2026-10-29: WLWWLWWWWWW
  2026-10-30: LWWWLL
  2026-11-02: L
  2026-11-03: W
  2026-11-04: W
  2026-11-05: L
  2026-11-06: WWWLLWWLWWWWWWLLWLWWW
  2026-11-09: L
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
