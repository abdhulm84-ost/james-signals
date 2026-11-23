# James channel — compiled correlation, 27–28 Oct 2026

One day of messages, one plan, three accounts. Correlated and verified against
live quotes (NDX/SPX ratio 3.8282 on the day; live 3.8443).

## 1. The map message (context layer, posted before any trade)

"Nasdaq 100 E-mini futures: now $29,600. S1 29450 / S2 29380 / S3 29300.
R1 29750 / R2 29820 / R3 29900. View: bullish above 29680, bearish below 29520."

Mapped onto SPX (ratio 29600/7732 = 3.8282):

| Level | NDX | SPX equivalent | Use |
|---|---|---|---|
| S3 | 29300 | 7654 | deep support |
| S2 | 29380 | 7675 | bull-put zone |
| S1 | 29450 | 7693 | bull-put zone |
| bear<29520 | 29520 | 7711 | his stated SPX range TOP (7710) |
| spot | 29600 | 7732 | |
| bull>29680 | 29680 | 7753 | |
| R1 | 29750 | 7771 | bear-call zone |
| R2 | 29820 | 7789 | bear-call zone |
| R3 | 29900 | 7810 | bear-call zone |

**Finding: the NDX map and the SPX plan ("range 7670 to 7710") are the same
view.** The NDX bear line (7711 SPX-equiv) IS his SPX range top. He works one
cross-instrument plan.

## 2. How the trades scored against his own map

| Trade | Entry level | His map said | Verdict |
|---|---|---|---|
| 7690/7680 bull puts | 7689 | S1–S2 zone (7675–7693) | **at the zone** — correct |
| 7725/7735 bear calls | 7735 | R1 is 7771 | **30 pts early** — sold short of his own R1 |
| 7720 iron fly body | 7720 | spot 7732 | pinned 8 pts below spot |
| 7725 iron fly body | 7725 | spot 7732 | 7 pts above spot |

**The best trades were at his stated zones. The overstated ones (early bear
calls, drift-up fly bodies) were where he chased.**

## 3. QQQ chain — 4 messages, one campaign, and a risk conversion

1. "Selling QQQ 720C for today n collecting around $120 premium — **For Funded**"
2. "35% to 40% on QQQ 720C sold, We collected $120 n now $85, Booking gains"
3. "Sell QQQ 721C/P, Buy 720P n Buy 722C n collect $73 for today, Risk is $27
   and reward is $73"
4. "QQQ Iron fly, book gains if RH"

Narrative: naked short call at $120 (UNLIMITED risk), closed at $85 (29% gain —
he claimed 35–40%), then replaced with a **defined-risk iron fly** at $73
credit with $27 risk = 2.70:1 reward:risk.

He converted undefined risk into defined risk while keeping the same bearish
view. The fly's own screenshot (15 lots, RH): 721C +724.99 / 722C −420 /
720P −19.99 / 721P −80 = **net +$205 = $13.67/contract = 18.7% of the $73
max profit, 50.6% of the $27 risk taken.**

## 4. Account routing (the hidden field)

| Phrase | Account |
|---|---|
| "**For Funded**" | funded/prop account |
| "if **RH**" | Robinhood |
| 13 lots (SPX reconciled) | Robinhood |
| 15 lots (QQQ fly screenshot) | Robinhood |
| 5 lots (SPXW leg screenshots) | funded or 3rd account |

Three books. That is why message-level P&L never matched one account screen.

## 5. AMKR 38C at 1.8 for 10/30 — the only full bracket in the set

"buy AMKR 38C at 1.8 for 10/30 / Targets $2.2/2.8/3.7 / SL 0.9"

- **The ONLY message with entry + targets + SL + expiry** (~25 messages, one bracket).
- Our pipeline executed it: filled 1.85 → closed 1.82 = **−$15 on 5 lots**.
- It is also the only message in standard single-contract format. Everything
  else is structures, percentages, or narration.

## 6. UPS/PYPL earnings trades (held, multi-day)

"Nice bounce on UPS and drop with PYPL, Pl book gains on both UPS and PYPL
earnings trades on 80%"

- Multi-day holds (narrated as earnings trades), trimmed 80%.
- Our earlier run: UPS/PYPL rejected at Tiger (SPY 720C/UPS leg issues) —
  structures were spreads, now parked.
- Pattern: earnings trades are a SEPARATE campaign with its own trim discipline.

## 7. Master message grammar (all classes now observed)

| Class | Example | Fields needed | Action |
|---|---|---|---|
| Map/levels | NDX S1-R3 + view | level, side, ratio to other symbols | context only |
| Single option bracket | AMKR 38C @1.8, T2.2/2.8/3.7, SL0.9 | ALL of: strike, right, expiry, entry, targets, SL | TRADEABLE as-is |
| Vertical spread | Sell 7705C Buy 7715C collect $480 | strikes, right, credit, expiry | park (no 2-leg) |
| Iron fly | Sell 7725C/P Buy 7715P 7735C | body, wings, credit, expiry, risk/reward | park (no 4-leg) |
| Naked/undefined | QQQ 720C $120 | strike, right, premium, "For Funded" | park (unlimited risk) |
| Decay marker | "40%" / "50%" / "70%" | claimed vs actual (bias +10 pts) | measure only |
| Exit/book | "100% on ...", "book gains" | % of what base | needs size |
| Sizing | "small size", "80%", "100%" | fraction | needs base size |
| Account tag | "For Funded" / "if RH" | routing | decides book |
| Prediction | "will make 100%" | forecast (weakest field) | record only |

## 8. Edge findings (updated with all messages)

1. **Intraday defined-risk cycle is the edge:** +$22,501.70 (+237%) in one day,
   13 lots, all closed by the bell.
2. **Overnight carries lose:** −$425 (5-lot screen) → full max loss at expiry;
   "they will make 100%" did not happen.
3. **Zone discipline predicts quality:** trades at his stated S/R zones worked;
   early sells and drift-up fly bodies were the margin calls of the day.
4. **Risk conversion is his real skill:** naked → fly (QQQ), shorts → defined
   spreads. The moments he ADDED risk were where the day got damaged.
5. **Claimed percentages run ~10 pts optimistic** (40 vs 25.2, 50 vs 46.6, 70 vs
   59, 35-40 vs 29) — systematic bias, not noise.
6. **Size is per-account** (13/15/5 lots) and NEVER in the text.

## 9. Tracker field spec (proven necessary and sufficient)

account | instrument | underlying_class | structure_type | body/strike | wings |
width | credit | expiry | qty | direction | action(entry/trim/exit/map) |
pct_claimed | pct_actual | result_per_contract | R | source(text/screenshot) |
missing[]
