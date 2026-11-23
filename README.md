# james-signals

Telegram **"James Private Signal Inbox"** → **Hermes Agent** (parser brain) → **paper CFD/ETF trades**.
Built 2026-10-22. Sister project to `signal-pipe` (same house pattern).

**Active setup (2026-10-23):** brain = Nous Portal `poolside/laguna-xs-2.1:free` (fallback
`stepfun/step-3.7-flash:free`) via `--provider nous`; executor = **BlackBull Markets MT5 demo**
(`executor: blackbull` in config.json) — cash index CFDs (US500 / USTEC.f) at James's NATIVE
levels, no index→ETF conversion, real CFD shorts. MT5 terminal must be running
(`C:\Program Files\BlackBull Markets MT5	erminal64.exe`) with Algo Trading enabled
(Tools → Options → Expert Advisors → "Allow algorithmic trading", and the two
"Disable ... when changed" boxes UNTICKED). Tiger paper remains wired as the
`executor: tiger` fallback (SPY/QQQ + index→ETF conversion + sim shorts).

## Flow

```
TG channel msg (+chart photo)
  → hermes chat --query-file -Q --in .   (AGENTS.md charter → strict JSON trade card)
  → validate + dedupe (state.json seen_msg)
  → map instrument        SPX500→SPY, NDX100→QQQ (config instrument_map)
  → convert index levels  k = ETF_quote / index_entry (or fixed divisor 10 / 44)
  → execute               long → Tiger paper MKT buy (account 21408391529011644)
                          short → ledger-simulated (Tiger paper cannot short)
  → manage                every 60s: quote vs stop/target → close (Tiger sell / sim book)
  → alerts                trade cards → Telegram Saved Messages
```

Hermes failure (e.g. free-tier Gemini 429) → signal queued in `parse_retry`
(4 attempts, 5 min × tries backoff) → PARSE_FAIL only after ~30 min of retries.
Never guesses an order.

## Files

| file | role |
|---|---|
| `james_pipe.py` | the whole pipeline (single file) |
| `config.json` | channel, instrument map, notional ($1k), stop %, dry_run, tiger props path |
| `AGENTS.md` | Hermes charter — auto-injected because of `--in .`; defines the JSON card |
| `tiger_openapi_config.properties` | copied from `F:\Projects on Trading\Telegram-AMD-Executor\tiger_config` (paper acct 21408391529011644, TBKIWI) |
| `telegram_watcher.session` / `telegram_push.session` | copies of the authorized kol-watch session |
| `state.json` | seen msgs, open/closed paper trades, parse_retry queue |
| `signals.csv` | full ledger: INGEST / NO_TRADE / ORDER / SIM / EXIT / PARSE_FAIL |
| `photos/` | downloaded chart images (fed to Hermes via `--image`) |
| `close.txt` | command file: `all` or a trade id closes that paper position |
| `selftest.py` | offline checks (extract/validate/map/convert/pnl math) |

## Ops

- Run: `python james_pipe.py` (`--dry` forces ledger-only). Autostarts via
  `Startup\james_signals.vbs`.
- Tiger: real-time quotes not subscribed → uses **delayed quotes** (`get_stock_delay_briefs`).
  US-market paper orders verified end-to-end (place/HELD/cancel, order 45056166244665344).
- Hermes brain: `--provider gemini -m gemini/gemini-3.8-flash` (free tier ~few hundred
  req/day — fine at signal volume; the pipe retries through 429s). Hermes app default
  model (`anthropic/claude-opus-4.6` → Gemini endpoint) 404s on fresh install — that's
  why the pipe pins provider/model itself. Copilot credential rejected claude models
  ("not available for integrator vscode-chat").
- Local spark brain (:1234) can't serve Hermes: Hermes demands ≥64K ctx, spark runs 32K.

## Options v2 (2026-10-24)

Option-contract calls ("SPY 765P at 1.5, Targets 2.2/2.8, SL 0.9") trade on **Tiger paper**
(the only venue with listed options): Hermes emits `option {underlying, strike, right, expiry}`
+ premium-level entry/stop/targets; expiry defaults to next Friday; qty = $1k notional /
premium/100 (1–10 contracts); LMT buy @ premium×1.03; premium marked via `get_option_briefs`;
stop/target on premium; exits SELL LMT @ mark×0.97; "Trim 80%" exits = partial close.
Closing a pending trade now CANCELS the resting Tiger order first (an orphan SPY long from a
resting order taught us that — reconciled via SELL 1 SPY, see ledger RECONCILE row).

## Known limits

- BB/Tiger index+stock flow: first target closes the full position; multiple targets not laddered.
- Default stop (1.5%) when James gives none — marked in the trade card `fill_note`.
- Option premium marks are Tiger DELAYED quotes — fine for paper, exit triggers lag accordingly.
- Option exits book at the limit price we send, not the eventual fill price (paper approximation).
- Sell-to-open (naked calls/puts) and SPX index options are ledger-only in v2.
- Tiger account also holds the user's OWN manual trades — reconcile only via our order ids.
- **Every defect found so far, with the message that exposed it and the test that
  guards it, is in `DEFECT_REGISTER.md`. Check it before re-fixing anything:
  `python defect_lookup.py --word <symptom>` or `--msg <id>`.
- Dealer-data dependent improvements (cushion vs expected move, walls, flip) are listed in
  `skylit_paid_backlog.md` — gated on the Initiate subscription, not on code.
