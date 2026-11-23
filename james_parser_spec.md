# James channel parser — specification v1

Source of truth for `AGENTS.md` (Hermes charter) and `james_pipe.py` schema.
Derived from ~25 messages of the 27–28 Oct 2026 campaign, every rule below
caused an error at least once before it was written down.

## 0. Scope and non-goal

Input: raw Telegram messages, which may be text, text + chart image, chart
image with text painted on it, screenshot, or screenshot with text painted on
it.

Output: a **typed record** — NOT a trade. The old design assumed message =
trade; the campaign showed a single trade spans 3–6 messages of different
classes. Parsing and execution are separate stages.

Non-goal: inferring size or fill price from text. They are not there.

---

## 1. Message classes (output `class`, mandatory)

| class | Definition | Action downstream |
|---|---|---|
| `map` | cross-instrument levels + directional view | store as context; never trade |
| `option_bracket` | single contract with entry AND (targets or SL) | **tradeable** |
| `vertical` | 2-leg, 2 strikes, sell one buy one | park |
| `fly` | 4-leg, one body sold twice, two wings | park |
| `condor` | 4-leg, 2 bodies, 2 wings | park |
| `naked` | 1-leg sell, no protective leg stated | park (undefined risk) |
| `decay` | percentage statement about an existing position | record measurement |
| `exit` | book/trim/close language | close (full or partial) |
| `size` | sizing instruction | record, do not compute |
| `prediction` | forecast ("will make 100%") | record as forecast |
| `narrative` | commentary with no tradable content | log only |
| `unparsable` | fails all of the above | alert, preserve raw |

**Rule: never collapse classes.** A message that names a structure AND quotes a
gain is a `fly` (or `vertical`) record plus a `decay` record — two rows, not one.

---

## 2. Record schema (mandatory fields)

```jsonc
{
  "class": "fly",                       // §1
  "msg_id": 1234,                       // telegram id
  "ts": "2026-10-27 14:47:00",
  "source": "text|chart_text|screenshot|screenshot_text",

  // instrument
  "underlying": "SPX500",               // normalised
  "underlying_class": "index|etf|equity|futures",
  "account": "rh|funded|other|unknown", // from tags, §5.2

  // structure
  "structure_type": "short_iron_butterfly|vertical_credit|naked_call|...",
  "body": 7725.0,                       // doubled strike (fly) / short strike
  "wing_low": 7715.0,
  "wing_high": 7735.0,
  "width": 10.0,                        // max |body - wing|
  "legs": 4,
  "direction": "short|long|flat",

  // economics (ALL per contract unless qty given, §4.1)
  "credit": 7.25,                       // signed: credit +, debit -
  "qty": 13,                            // null if unknown — NEVER guess
  "expiry": "2026-10-27",               // null if unknown
  "risk_stated": 27.0,                  // from "risk is $27"
  "reward_stated": 73.0,                // from "reward is $73"

  // measurement (decay/exit rows)
  "pct_claimed": 70,                    // what he said
  "pct_actual": 59.0,                   // recomputed, §4.2
  "price_shown": 2.97,                  // screenshot mark if present

  // provenance
  "missing": ["qty", "expiry"],         // what the message did NOT give
  "raw": "..."
}
```

---

## 3. Structure determination (§ of the reading method)

1. **`C/P` means TWO legs** at that strike. If the parser returns 1 leg where
   the text has `C/P`, the text wins; record the discrepancy in `missing`.
2. **Recover strikes from the TEXT**, not the returned leg list — the brain is
   non-deterministic on multi-leg input (same text returned 2, 3 and 4 legs on
   three runs). Regex the 4-digit numbers; keep duplicates (a body repeats).
3. **Body = middle of three strikes** for a fly. Vertical = the short strike.
4. `legs >= 4` + 3 distinct strikes = `fly`. `legs == 2` + 2 strikes = `vertical`.
5. A sell with no matching protective leg = `naked`.

### 3.1 Iron fly economics (differs from vertical)

- `profit_at_pin = credit * 100 * qty`
- `max_loss = (width - credit) * 100 * qty`
- `prob = credit / width` — **can exceed 100%** (body sold twice). Do not
  clamp, do not use the vertical formula.
- **Wing width is the quality signal**: 5-wide ≈ 14:1 at the pin, 10-wide ≈
  2.1:1. Flag `width` in every fly record.

---

## 4. Decoding rules (extraction-time, not analysis-time)

### 4.1 Per-contract rule
Every dollar figure in text is **per contract** unless it says otherwise:
"$720 profits on each contract", "collect $480", "$440 on each of 7725 iron
fly". Store as `credit` / `result_per_contract`. Never multiply without `qty`.

### 4.2 Percentage rule
**Stated percentages are optimistic by ~10 points** (observed: 40/25.2, 50/46.6,
70/59, 35-40/29). Always recompute from screenshot premiums:
`pct_actual = (credit - price_now) / credit * 100`. Store both.

### 4.3 Decay-marker rule
Successive shots with "40% / 50% / 70%" are **decay markers on ONE position**,
not lot trims. **Check `qty` on the screenshot: if unchanged, nothing was
trimmed.** Only `% on X` with a shrunken qty is a real trim.

### 4.4 Exit semantics
- `"100% on X"` = full close of X
- `"% on X"` = partial, subject to §4.3
- `"book gains"` / `"take gains"` / `"booking gains"` = exit
- `"buy back at $N"` / `"collect $N"` = **exit price stated in text** (L12) —
  P&L = (entry_credit − exit_debit) × 100 × qty, computable from text alone.
- `"Nth target done"` = **target-ladder partial exit**, see §4.7
- Rounding: % of small lot counts is ambiguous (40% of 2 lots = 0.8). Record
  the ambiguity in `missing`; do not silently round.

### 4.7 Position matching — "Nth target done" (resolves L21)

The message carries NO instrument, target value, or trim fraction. With several
positions open it is genuinely ambiguous, so **infer with confidence and never
trade on low confidence.**

`targets[]` is a **ladder of trim levels**. A hit at `targets[N-1]` is a partial
close.

**Matching algorithm** (in order of strength):
1. **Image** — if a screenshot accompanies it, read the position identity from
   the image (the definitive match; depends on L4/A4).
2. **Price match** — scan open positions with ≥ N targets; the one whose
   `targets[N-1]` is currently breached by live price is the match. (Strong test:
   GLW targets[0]=3.9 with premium now 4.1 → almost certainly GLW.)
3. **Recency** — tiebreak: the most recently opened target-bearing position.
4. **Ambiguous → ALERT, do not trade.** A wrong partial-close on the wrong
   position is worse than missing the exit. Push a card naming the candidates.

**Trim fraction** (default, overridable by a stated %): equal ladder, so
`"Nth target done"` trims `1/len(targets)`. For a 3-target ladder: 1/3, then
1/3, then close the remainder. A later stated fraction ("book 80%") overrides.

**Fields this needs:** `targets[]` (already have), `qty` (image), and a
`pct_actual`/`closed_fraction`. Never silently round a partial.

### 4.5 Risk/reward verification
`"Risk is $27 and reward is $73"` is the most trustworthy line in any message
because it encodes width and credit together. Verify:
`reward = credit * 100`, `risk = (width - credit) * 100`. If the check fails,
the structure is mis-parsed.

### 4.6 Text/screenshot mismatch
When text and screenshot disagree, **the screenshot is the fill**. Record both.
Known cases: quoted 5.70/5.80 vs limit 5.62 vs fill 5.70; text "720C" vs screen
"721C".

---

## 5. Routing and provenance

### 5.1 Venue
| Underlying | Venue | Notes |
|---|---|---|
| SPX500/SPX/ES index options | Tiger `SPXW` | 5-pt strike grid, 0.1 tick |
| SPY/QQQ equity options | Tiger | |
| NDX/NDXW | **unsupported** | park, never proxy to SPY |
| index/stock CFD levels | BlackBull `SPX500`/`NAS100` | full trading, tight spread |

### 5.2 Account tags (decides which book a trade belongs to)
- `"For Funded"` → `account: funded`
- `"if RH"` / `"book gains if RH"` → `account: rh`
- absent → `unknown`. **Three books were active in one day** (RH 13-lot SPX,
  RH 15-lot QQQ, funded 5-lot SPXW). Never reconcile across books.

---

## 6. Map class (`map`) handling

A `map` record gives levels + a view, e.g.
`"Nasdaq 100 E-mini futures, now 29600, S1 29450 ... R3 29900, bullish above
29680, bearish below 29520"`.

Store as `levels[]` + `view`. **Correlate to the traded symbol by the spot
ratio** (day of record: NDX/SPX = 3.8282). Score every later trade against
these zones: trades at stated S/R zones held up; sells 30 pts before his own R1
were the thin ones.

---

## 7. Tradeability gate (what may be executed)

Execute ONLY when ALL hold:
1. `class == option_bracket` (single contract, entry + at least one of targets/SL)
2. `underlying` resolves on the venue (§5.1)
3. `expiry` present and not past
4. `direction != flat`
5. no `spread`/multi-leg content anywhere in the message

Everything else → `parking_lot.csv`, with economics preserved (§2 fields),
never dropped.

**Guiding principle for ambiguous records:** a wrong trade is worse than a missed
one. Whenever a record cannot be matched or sized with confidence, ALERT and
leave the book alone — do not guess. This governs §4.7 (position matching),
§4.3 (trim vs marker), and any multi-position ambiguity. Push a 🧩 card; write a `PARKED` row to `signals.csv`.

**Known tradeable exemplar** (the only one in 25 messages):
`buy AMKR 38C at 1.8 for 10/30 / Targets $2.2/2.8/3.7 / SL 0.9`

---

## 8. Known failure modes (regression list)

| # | Failure | Guard |
|---|---|---|
| 1 | brain drops a `C/P` leg | text-strike recovery (§3.2) |
| 2 | brain labels a structure `action: none` | text-first classification |
| 3 | multi-leg collapsed to one leg | refuse execute; park (§7) |
| 4 | option credit read from `card.entry` | use `spread.net` / §4.1 |
| 5 | naked short executed as a long call | `naked` class + undefined-risk flag |
| 6 | pct_claimed taken as PnL | always recompute (§4.2) |
| 7 | cross-book reconciliation | account tag enforced (§5.2) |
| 8 | text figure treated as total | per-contract rule (§4.1) |
| 9 | timezone age skew (13h in NZ) | `calendar.timegm`, not `mktime` |
| 10 | close-before-fill sends naked SELL | cancel the resting BUY, book PnL 0 |

---

## 9. Acceptance criteria

A parse is correct when, on the 27 Oct corpus:

1. The `map` record reproduces NDX→SPX zones within 1%.
2. Every fly/vertical/condor parks with `width`, `credit`, `prob` correct.
3. The AMKR bracket trades and books PnL from the real fill.
4. Per-contract sums × per-account size reconcile the day to within fees
   (target: 0.1%).
5. `pct_claimed` and `pct_actual` are both stored and the delta is ~+10 pts.
6. No message is dropped: every one yields a record or an `unparsable` alert.

---

## 10. Risk policy — phased rollout (governs execution)

**Phase 1 — RECORD EVERYTHING, TRADE ONLY DEFINED RISK.**
- Record *all* signals: targets, lottos, maps, predictions — nothing dropped.
- **Execute all defined-risk structures:** brackets (with SL + targets),
  verticals, and flies. The hard gate is RISK TYPE (capped, defined loss), NOT
  risk quality. A 39%-credit vertical is still a valid, defined-risk trade.
- **`credit/width` is a SIZING signal, not a filter.** Higher quality → larger
  size, lower quality → smaller size. Never use it to reject a defined-risk
  trade; using it as a pass/fail threshold would skip legitimate opportunities.
  (It predicts R:R, so it ranks and sizes — it does not gate inclusion.)
- **Lottos and high-risk-tier calls are RECORD-ONLY** (→ parking_lot) with their
  outcomes logged for calibration. Do not trade them.
- Portfolio discipline: cap aggregate risk, respect same-expiry concentration
  (L22), and never chase a `pnl_goal` (L31).

**Phase 2 — only after the core is PROVEN PROFITABLE.**
- Reassess lottos (the "0 / 100%" class) once the defined-risk book shows real,
  repeatable profit and the lotto calibration has enough rows to be meaningful.
- If adopted, lottos get the smallest size tier (`risk_tier: lotto`).

Rationale: lottos are binary (100%+ or 0) and their expected value is unknown
until measured. The defined-risk edge (credit/width → favourable R:R) is the
reliable part. Prove that first; add the lottery tickets only once the account
can carry them.

This policy is the top-level gate: **the parser records everything, but the
executor only ever takes trades this policy allows.**

**Balance guard — do not over-filter.** Risk control must reject DANGEROUS risk
(undefined/naked, lotto until Phase 2, oversized, chasing a pnl_goal), never
reject VALID risk (a defined, capped-loss structure). If the filter would skip a
trade that has a defined max loss and a stated exit, that filter is too strict.
When in doubt between taking and skipping a defined-risk trade, TAKE IT SMALLER —
downsize, do not drop.
