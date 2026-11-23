# Skylit paid-sub backlog — what stays approximate until Initiate

Companion to `james_system_limitations.md`, `parking_lot.md` and the `skylit-evaluation`
memory. Every row here is a place the pipeline is currently **guessing with geometry**
where dealer data would let it **measure a probability**. None of it is broken; all of it
gets better the day a paid key exists.

**Order of operations (decided 2026-11-09):** squeeze the edge out of SPX first — 62% on
30 clean trades — then revenue, then buy `Initiate $349`, then work this list top-down,
then push the target to 75%. Nothing here is bought before that. The user explicitly
accepted parking through a blocked trade in the meantime rather than trading blind.

**Today:** free web account, no API. Manual read of `app.skylit.ai/flow/live` beside live
SPX positions, logged with `skylit_note.py` into `skylit_notes.csv`. Probe harness sits
read-only at `C:\Trading\skylit-probe` (`--dry` is the free eligibility call).

## Gated items

| # | Item | What it replaces today | Where it plugs in | Today's stand-in |
|---|---|---|---|---|
| S1 | **Cushion vs expected move** — compare break-even distance with the session's expected range, park when cushion < ~0.5× expected move | the flat one-credit heuristic | `itm_guard` block in `open_multileg_tradier`; `itm_cushion_credits` | `itm_cushion_credits = 1.0` (a credit is ~the ground the index covers in the first minutes) |
| S2 | **Wall proximity at entry** — a short strike sitting just behind a dealer wall is protected; one with open space behind it is naked in practice | nothing; strikes are taken as written | same guard, same place, plus the level store | James's own written levels, when he writes any |
| S3 | **Flip / gamma regime** — do not sell premium into negative gamma with price under the flip | the drift guard alone | entry guard + `manage_once` exit timing | none — this is the pure guess |
| S4 | **Exit timing on a wall touch** — bank into strength when price reaches the wall/King node | the level ladder waits for its armed number | `_exit_target_hit` / `manage_once` | manual overlay, `skylit_note.py`, being tested over 15-20 SPX trades |
| S5 | **IV rank / rich premium** — prefer credit when IV is elevated, size up within policy | credit/width as the only quality proxy | `tier_of`, quality sizing | `credit / width` 0.55 threshold |
| S6 | **Flow confirmation** — break ties between two structures that both pass | coin-flip on the wider one | pre-placement | none |
| S7 | **Programmatic level scoring** — score stored LEVELS rows against nodes instead of treating every number alike | all levels equal | `text_levels` consumers | manual |

S1 is the one the user named directly: *"once we have skylit paid sub we can add credit
cushion"*. It is first because it improves the guard that already exists rather than
adding a new subsystem.

## Un-gate checklist (do in this order, once funded)

1. `SKYLIT_KEY` into the environment; `python probe.py --dry` (free: proves key + plan).
2. `--symbols` — confirm SPXW resolves on the plan (no futures on any plan; ES/NQ stay
   out of scope).
3. One live `--symbol SPXW` levels pull (~1 credit, ~$0.001) — read the returned
   King/flip/wall nodes by eye against 3-4 stored LEVELS rows of the same session.
4. Only if step 3 reads correctly: wire S1, then S2, each behind a config flag defaulting
   off, each with a pinned test and an off-switch.
5. Meter `X-Credits-Remaining` in the log; 120 req/min cap; read-only (no order path
   depends on a Skylit call).

## Standing caveats

- Beta API, **all sales final**, $149/mo (Community) buys flow only — the level data
  needs Initiate $349. No point paying Community alone.
- Credits are one-off ($5 of calls at $0.001 each), then pay-as-you-go; a levels pull per
  order is affordable, a pull per poll is not — cache aggressively.
- Skylit never replaces the guard's own arithmetic; it supplies the expected move and the
  wall distance, and the existing rules stay as the floor when a call fails.
