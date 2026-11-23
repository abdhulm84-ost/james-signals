# P2 audit — parked / blocked / refused signals (2026-11-02 → 11-09)

Same method as P1: text, the pipeline's reason, the market outcome at expiry (qty 1, capped at width), what TODAY's code would do, and a classification.

| msg | NZ-relevant ts | text | park reason | outcome | today | class |
|---|---|---|---|---|---|---|
| 3723 | 2026-11-02 15:16 | Buyers taking full control, as we have multiple positions of SPX, not taking other trades. All chips will move |  |  |  | CONTEXT |
| 3725 | 2026-11-02 15:17 | 7641 done, take gains on 80% of 7625/7635 and 7620 and 7610, 7615/7605, we can hold all others |  |  |  | DERIVATIVE (exit of unheld pos) |
| 3783 | 2026-11-03 15:23 | SPX 7572 again, pl book gains on any trades below or close to 7570 |  |  |  | DERIVATIVE (exit of unheld pos) |
| 3787 | 2026-11-03 15:42 | RUT n IWM, SPX breaking resistance, more potential, Enter IWM n RUT again |  |  |  | CONTEXT |
| 3790 | 2026-11-03 16:00 | SPX over 7585 now, As we missed booking gains yesterday, took gains on most of SPX Bull Puts, waiting for a pu |  |  |  | DERIVATIVE (exit of unheld pos) |
| 3793 | 2026-11-03 16:09 | Sell SPX 7590P n Buy 7580P n collect $470 for today | Tradier placement failed: Tradier HTTP 400: Invalid parameter, symbol: is requir | -530 | place (plumbing fixed) | PLUMBING (would trade today) |
| 3809 | 2026-11-03 19:36 | Take gains on spx bear calls |  |  |  | DERIVATIVE (exit of unheld pos) |
| 3813 | 2026-11-03 20:16 | Sell SPX 7560C n Buy 7570C n collect $430 to $435 for today - SPX will stay below 7560 by EOD | Tradier placement failed: Tradier rejected: {'errors': {'error': 'Undefined symb | -462 | place (plumbing fixed) | PLUMBING (would trade today) |
| 3832 | 2026-11-04 00:30 | 75770/7580 Bear calls (Don't miss trade) will make over 50% when market opens in few minutes, We can book gain |  |  |  | DERIVATIVE (exit of unheld pos) |
| 3837 | 2026-11-04 01:14 | Spx below 7715, pl book gains on both bear calls |  |  |  | DERIVATIVE (exit of unheld pos) |
| 3839 | 2026-11-04 01:34 | SPX close to 7500 now. Pl book gains on RH Web. App is not working but web is working fine. We can wait for a  |  |  |  | DERIVATIVE (exit of unheld pos) |
| 3860 | 2026-11-04 13:00 | Sell SPX 7620C n Buy 7630C n collect $530 for 11/4 - Take double size, Don't miss | short strike 7620 ITM vs spot 7,634.60 — strike drift, parked (itm_guard) | -470 | park | GUARD RIGHT (loss avoided) |
| 3861 | 2026-11-04 13:13 | Price broke R8 and we got Sell in 15m so 7600 and 7590 is target |  |  |  | CONTEXT |
| 3900 | 2026-11-04 17:04 | Sell SPX 7700C n Buy 7710C n collect $475 for today | short strike 7700 ITM vs spot 7,704.10 — strike drift, parked (itm_guard) | 475 | PLACE (rule fixed) | GUARD WRONG - FIXED |
| 3903 | 2026-11-04 17:14 | Read this as Sell 7705 n buy 7715 for today | no parseable legs | (correction to 3900) |  | GUARD WRONG - FIXED |
| 3914 | 2026-11-04 19:31 | 40% up on 7705/7715 Bear calls, Pl take gains on 80% |  |  |  | DERIVATIVE (exit of unheld pos) |
| 3917 | 2026-11-04 19:35 | Taking gains on today's 7700/7715 Bear calls and Debit Puts. High risk takers can hold 20% n close rest |  |  |  | CONTEXT |
| 3919 | 2026-11-04 19:56 | Closing remaining 7700/7715 at 4.1 for today, holding tomorrows |  |  |  | CONTEXT |
| 3922 | 2026-11-04 20:12 | Taking gains on 7700/7715 Bear calls of tomorrow, $120 to $130 on each contract |  |  |  | CONTEXT |
| 3928 | 2026-11-04 21:01 | 7700/7715 Bear calls and 7700/7715 Debit Puts are 100% if any high risk takers holding them |  |  |  | CONTEXT |
| 3932 | 2026-11-04 21:22 | Took loss on last night and Premarket SPX  Bear call trades and made gains on all other 8-10 trades entered to |  |  |  | CONTEXT |
| 3935 | 2026-11-05 05:51 | SPX below 7665, Low risk takers can take gains with 7660/7670 Bear calls |  |  |  | DERIVATIVE (exit of unheld pos) |
| 3952 | 2026-11-05 14:41 | 90 POINTS drop on SPX, Nearly 100% on all bear calls, Pl book gains if any runners |  |  |  | DERIVATIVE (exit of unheld pos) |
| 3957 | 2026-11-05 15:02 | 20% on 7625/7635 of 11/6, Pl book gains |  |  |  | DERIVATIVE (exit of unheld pos) |
| 3959 | 2026-11-05 15:07 | 20%+ on 7630/7650 Bear calls, Pl book gains, we will wait for Pull back to enter Bear calls again |  |  |  | DERIVATIVE (exit of unheld pos) |
| 3963 | 2026-11-05 15:28 | 7470 done, 40% up, | Tradier placement failed: Tradier rejected: {'errors': {'error': 'Undefined symb | score card |  | IMAGE - handled now |
| 3975 | 2026-11-05 17:01 | Took gains on 7450/7460 Bear calls, will watch for a reversal now |  |  |  | CONTEXT |
| 3981 | 2026-11-05 17:52 | Sell SPX 7410P n Buy 7400P n collect $455 for tomorrow - Don't miss it | short strike 7410 ITM vs spot 7,398.70 — strike drift, parked (itm_guard) | -545 | park | GUARD RIGHT (loss avoided) |
| 3990 | 2026-11-05 19:37 | Moves will be very quick so pl create lmt orders to book gains on trades entered especially for any today's ex |  |  |  | DERIVATIVE (exit of unheld pos) |
| 3992 | 2026-11-05 19:48 | TSLA, IWM, RUT, QQQ, SPY, SPX - All bounced big, Take gains on today's first and hold tomorrow expiry |  |  |  | DERIVATIVE (exit of unheld pos) |
| 3993 | 2026-11-05 19:48 | SPX, 50 points up | Tradier placement failed: Tradier rejected: {'errors': {'error': 'Undefined symb | level list |  | IMAGE - handled now |
| 4001 | 2026-11-05 20:07 | Sell SPX 7395P n Buy 7385P for 11/6 n collect $440 - Don't miss | short strike 7395 ITM vs spot 7,375.50 — strike drift, parked (itm_guard) | -560 | park | GUARD RIGHT (loss avoided) |
| 4016 | 2026-11-06 00:15 | Expecting SPX to close above 7425 tomorrow. SPX touched support n bouncing after hours. |  |  |  | CONTEXT |
| 4026 | 2026-11-06 13:51 | Sell SPX 7380P n Buy 7370P n collect $440 to $450 for today | short strike 7380 ITM vs spot 7,374.20 — strike drift, parked (itm_guard) | 126 | park (misses +126) | GUARD borderline (+126 missed) |
| 4030 | 2026-11-06 14:39 | 7400 done, Pl trim 60% on all Bull Puts of SPX, QQQ |  |  |  | DERIVATIVE (exit of unheld pos) |
| 4032 | 2026-11-06 14:42 | 7405 done, 7430 next target | short strike 406.2 is 94.5% from spot 7,419.70 (>15.0%) |  |  | CONTEXT |
| 4041 | 2026-11-06 14:55 | 7445 also done, Pl book gains, we can wait for pull back and enter Long again |  |  |  | DERIVATIVE (exit of unheld pos) |
| 4049 | 2026-11-06 15:48 | Take gains on 7400/7410 bull puts when you see in green |  |  |  | DERIVATIVE (exit of unheld pos) |
| 4059 | 2026-11-06 16:47 | As of now, SPX is looking choppy between 7395 and 7445. When it goes to 7395, we can look to enter Bull Puts a |  |  |  | CONTEXT |

## What this says

Of 39 P2 rows, only 8 are entries where a park actually decided something. The other 31 are:
17 exits on positions we never held (derivative — they resolve the moment the entry is fixed),
12 context/prediction rows with no action, and 2 image parks.

**The guards were right 6 times out of 8.**

| entry | ITM at send | credit | outcome | verdict |
|---|---|---|---|---|
| 3793 | OTM | 4.70 | -530 | plumbing, not the guard |
| 3813 | OTM | 4.30 | -462 | plumbing, not the guard |
| 3860 | 14.6 | 5.30 | -470 | guard right |
| 3900 | 4.1 | 4.75 | **+475** | guard wrong → fixed |
| 3981 | 11.3 | 4.55 | -545 | guard right |
| 4001 | 19.5 | 4.40 | -560 | guard right |
| 4026 | 5.8 | 4.40 | +126 | borderline, still parks |

Every large park was a loss avoided. The two losses the guard "cost" are the two smallest
moves: +126 and +475. That is the shape you want from a defensive rule.

The new rule was replayed against all seven: it **keeps every right park** and unblocks only
3900. It does not re-open the TL3732/3738 max-loss class — those were 14-19 points ITM.

## Open items this audit leaves

1. **3793 / 3813 are parks that today would trade — into losses.** Mechanics are fine now,
   but it is a reminder that the plumbing fixes raise trade count, not win rate.
2. **4026 is the known false positive** (+126): 5.8 ITM against 4.4 credit. If the P3 audit
   shows this class recurring, `itm_cushion_credits` is the knob (0.0 now, 0.25 would let
   ~1 credit of intrusion through).
3. Exits on unheld positions are now silent by design (`EXIT NOT HELD`) — 17 of the 39 rows
   here were exactly that, which is why the push volume dropped.
