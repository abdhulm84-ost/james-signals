# How to read the James channel — the reading method

Hard-won from ~25 messages of the 27 Oct campaign. Each input type carries
different information, and the errors come from mixing them.

## 1. The five input types and what each carries

| Type | Carries | Never carries |
|---|---|---|
| **Text only** | structure, strikes, credit, expiry, direction, intent | size, fill price |
| **Text + chart, message ON the chart** | direction, expected path (e.g. "drop to fair value then bounce"), zone boundaries | strikes, size |
| **Chart only** (TradingView) | live price, OHLC, indicator lines, fair-value curve, session colour | order details |
| **Screenshot only** | qty, expiry date, avg credit, fill price, per-leg P&L | intent, timing |
| **Screenshot + message on it** | trim percentages, booking language, account tags ("if RH") | base size |

**Reading order: text for INTENT first, screenshot for the NUMBERS second.**
The screenshot is the fill confirmation. Never price a trade from the text
alone — the text quotes per-contract figures and the fill is often different.

## 2. The decoding rules (each caused an error at least once)

1. **Every dollar figure in text is PER CONTRACT.** "$720 profits on each
   contract", "collect $480", "$440 on each of 7725 iron fly". Multiply by
   size — and size is only ever on screenshots (and only per-account).
2. **Percentages are optimistic by ~10 points.** Claimed vs actual across four
   samples: 40/25.2, 50/46.6, 70/59, 35-40/29. Recompute from the screenshot
   premiums; never trust the stated %.
3. **"40% / 50% / 70%" on successive shots are DECAY MARKERS on one position,
   not lot trims.** Check the qty field — if it is unchanged, the position was
   never trimmed.
4. **"100% on X" = full close of X. "% on X" = partial.** "Book gains" = exit.
5. **"C/P" means TWO legs** (call AND put at that strike) — a butterfly body.
   The parser may collapse it to one; recover strikes from the text. Middle
   strike of three = body.
6. **Iron fly:** 4 legs, body sold twice, wings one each. `prob = credit/width`
   runs ABOVE 100% (body sold twice). **Wing width decides quality:** 5-wide
   gave 14:1 at the pin, 10-wide gave 2.1:1 — 7x difference for similar credit.
7. **Risk/reward stated in text is trustworthy** ("Risk is $27 and reward is
   $73") — it encodes width and credit together. Verify: reward = credit x 100,
   risk = (width - credit) x 100.
8. **Account tags route the trade:** "For Funded" = prop account, "if RH" =
   Robinhood. Screenshots show whichever book that message belongs to. Three
   books in one day: RH 13-lot SPX, RH 15-lot QQQ, funded 5-lot SPXW.
9. **Predictions ("will make 100%") are the weakest field** — record as
   forecast, never as fact. The 10/28 overnight "will make 100%" expired ITM.
10. **"small size" / "80%" / "50%"** are sizing instructions with NO base —
    annotate, do not compute.

## 3. The correlation method

1. **Read the map layer first.** Cross-instrument levels (NDX S1-R3) map onto
   SPX via the spot ratio (29600/7732 = 3.8282). His SPX range boundaries ARE
   the mapped NDX lines. One plan, expressed twice.
2. **Score each trade against his own map.** Zone discipline predicts quality:
   trades at stated S/R zones held up; sells 30 pts before his own R1 were the
   thin ones.
3. **Reconcile per-contract sums x per-account size** against the account
   screenshot. 10/27 closed at 0.1% (fees) once size was known.
4. **Watch for risk conversion.** His strongest moves convert undefined risk to
   defined (naked short -> iron fly). His weakest add risk (overnight holds).
5. **Split results intraday vs overnight.** Intraday defined-risk cycle:
   +237% in a day. Overnight carries: every one lost.

## 4. Message grammar (all classes observed)

map/levels | single-option bracket (only 1 in 25 msgs — the tradeable one) |
vertical | iron fly | naked/undefined | decay marker | exit/book | sizing |
account tag | prediction/forecast

Anything not a single-option bracket is currently PARKED (parking_lot.csv),
recorded with economics, never dropped.

## 5. What is still missing from every message

**Size** (screenshot only), **account** (tags only), **exit price** (screenshot
only), **base for percentages** (never stated). These four are why a tracker
needs a screenshot-extraction step: text alone caps out at per-contract
economics and approximate R.
