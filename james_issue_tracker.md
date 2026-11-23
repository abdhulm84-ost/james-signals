# James pipeline — outstanding issues & clean-rebuild roadmap

Status as of 2026-10-28 night. Everything here is learned from the 27–28 Oct
campaign plus live-message testing. Companion docs: `james_parser_spec.md`,
`james_reading_method.md`, `james_campaign_2710.md`, `parking_lot.md`.

## Fixed and proven (do not re-do)

- Fabricated-legs bug: brain invents legs from strike-less text → text-first guard.
- Entry-gate bug: status/prediction messages labelled `entry` opened live shorts →
  command/structure gate. (msg 3458 was the case.)
- Map capture: NDX level blocks stored as MAP rows, not dropped.
- Option PnL from real fills (not limit price); pending_close reconciliation.
- Stale option entries expire + cancel; close-before-fill cancels, books PnL 0.
- Timezone age skew (13h in NZ) → `calendar.timegm`.
- Symbol map: BlackBull `SPX500`/`NAS100` full-trading, tight spread.
- Preflight + health monitor + MT5 auto-relaunch + watchdog.
- Parking lot for untradeable structures.
- Test hygiene: stubbing `save_state` does NOT make executor calls safe (they
  place real MT5 orders). Use a dry executor in tests.

## A. Parser correctness (do before the clean rebuild)

| # | Issue | Evidence | Fix |
|---|---|---|---|
| A1 | Charter never emits typed records | spec v1 written, not applied | rewrite `AGENTS.md`: `class`/`account`/`structure_type`/`pct_claimed`/`pct_actual`/`missing[]` |
| A2 | Brain non-deterministic on multi-leg | same text → 2/3/4 legs across runs | text-first strike recovery (code done); charter must also say "never invent legs" |
| A3 | Chart-painted text not extracted | "message on the chart" is the thesis layer | pass chart image + ask brain to read the painted text into `chart_text` field |
| A4 | Screenshot fields not extracted | qty/expiry/fill/account only live in images | image pass returning `qty`, `expiry`, `avg_credit`, `fill_price`, `account` |
| A5 | Account tags ignored | "For Funded" / "if RH" decide the book | parse tags → `account` field, never cross-reconcile |

## B. Structural capability (the edge is locked behind these)

| # | Issue | Why it matters | Fix |
|---|---|---|---|
| B1 | **No multi-leg execution** | iron flies were the BEST trades (14:1, 5-wide); currently park-only | 4-leg executor + spread book keyed on body/wings |
| B2 | No spread book | trades span 3–6 messages; exits can't match entries | instrument-with-legs model, not per-message trades |
| B3 | No overnight/carry concept | every overnight lost; 0DTE assumes same-day | hold-across-close state + expiry settlement |
| B4 | No per-account books | 13/15/5 lots across 3 accounts → P&L never reconciles | `account` dimension in the book |
| B5 | Vertical/condor not executed | 2-leg and 4-leg need their own risk maths | per-structure profit/risk formulas (fly prob > 100%) |

## C. Measurement integrity

| # | Issue | Evidence | Fix |
|---|---|---|---|
| C1 | Claimed % optimistic ~10 pts | 40/25.2, 50/46.6, 70/59, 35-40/29 | always recompute from screenshot premiums; store both |
| C2 | Per-contract vs total confusion | every text figure is per contract | `qty` required before any P&L; multiply explicitly |
| C3 | Size never in text | screenshots only | image extraction (A4) |
| C4 | No reconciliation harness | day only closed to 0.1% after manual work | script: per-contract sums × per-account size vs account screenshot |

## D. Engineering hygiene (the "clean code" rebuild)

| # | Issue | Fix |
|---|---|---|
| D1 | `james_pipe.py` accreted ~1500 lines of patches | rebuild around the typed record + a dispatcher per `class` |
| D2 | Tests can place real orders | dry executor behind a flag; never touch live MT5/Tiger in tests |
| D3 | Parse/execute coupled | split: parse → typed record → separate executor stage |
| D4 | Schema sprawl across state.json/CSVs | one record schema (spec §2), one store, one ledger |

## Sequencing

1. **A1–A5** (parser correctness) — makes the data trustworthy.
2. **C1–C4** (measurement) — makes the data measurable.
3. **B1–B5** (capability) — unlocks the actual edge (flies).
4. **D1–D4** (clean rebuild) — restructure around the typed record.

Reason for this order: B (capability) is where the money is, but building it on
untrustworthy parse data (A) or unmeasurable P&L (C) reproduces today's errors at
scale. D last because it restructures the whole thing once the model is right.

## The core model to build toward

A message is NOT a trade. The unit is a **typed record**; the trade is a
**managed structure** (legs, body/wings, width, credit, expiry, qty, account)
that a sequence of records opens, rolls, trims and closes. The parser's job is
faithful extraction (text-first, screenshot-second); the book's job is matching
records to structures; the executor's job is only ever acting on a confirmed
single-contract bracket or a confirmed multi-leg with defined risk.
