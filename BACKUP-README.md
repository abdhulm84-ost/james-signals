# james-signals — GitHub backup repo (created 2026-11-23)

Code backup of the live pipeline at `C:\Trading\james-signals`.
This repo is intentionally **code only** — safe to upload to GitHub.

## What's in
- All pipeline code (`*.py`), tests (`test_*.py`), ops scripts (`*.bat`, `*.vbs`, `*.ps1`)
- `config.json` (no secrets — channel/models/instrument map only)
- Docs: `README.md`, `DEFECT_REGISTER.md` (D01–D150), all `*.md` plans
- `postmortems/` and `audit/`

## What's deliberately OUT (do not add them)
- **Credentials**: `tradier_config.json`, `bb_config.json`, `tiger_openapi_config*.properties`, `*.session` (Telegram sessions), `.env`
- **Runtime data**: `state.json`, `book.json`, all `*.csv` ledgers, `spx_gate.json`, queues, logs
- **Bulk/media**: `lse_flow.csv` (~814 MB tape), `photos/`, `yt_analysis/`

Those all live in the complete disaster-recovery copy:
**`F:\james-signals-runtime-backup\`** (full 949 MB mirror of C:\Trading\james-signals, including credentials — keep that folder OFF GitHub).

## Push to GitHub manually (as agreed)
```
cd F:\james-signals
git remote add origin https://github.com/<you>/james-signals.git
git push -u origin main
```
If you ever force-add an excluded file by mistake: `git rm --cached <file>` before pushing.

## Restore
Code → clone/copy this repo; runtime+credentials → copy
`F:\james-signals-runtime-backup\` over it; restart via the Startup VBS
(`james_signals.vbs`).
