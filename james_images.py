"""
james_images.py — screenshot field extraction (L4 / A4).

The review established that a large share of James's messages are image-only,
and the fields that matter most live ONLY in the screenshot: qty, expiry, fill
price, account, and the exact percentage. Text alone caps out at per-contract
economics and approximate R.

This module runs a FOCUSED extraction pass through Hermes (which has vision),
asking it to read the screenshot and return the structured fields. It is a
separate pass from the main message parse because a screenshot is a dense
position/order screen — a dedicated prompt extracts those fields far more
reliably than asking the main parse to do two jobs at once.

The result merges into the v2 Record (james_core.Record) so the book sees the
real qty/fill/account rather than inferring them.

Wiring: james_pipe calls extract_image_fields(photo) when a message has an image,
then folds the returned fields into the Record. Extraction is best-effort: if it
returns nothing, the record falls back to text-only fields and marks them missing.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
QUERY_FILE = HERE / "_image_query.txt"

# The schema we ask Hermes to fill from the screenshot. Everything is optional —
# a screenshot may show only some of these. Never invent a value.
EXTRACT_PROMPT = """You are a trading-screenshot reader. Look at the attached image
(a broker position screen, order ticket, or a chart with a message painted on it)
and extract ONLY the fields below that are VISIBLE in the image. Return a single
JSON object. Do NOT invent values — if a field is not visible, use null.

Fields:
  underlying   : ticker or index symbol (e.g. "SPX", "QQQ", "AMZN", "SPXW")
  instrument   : the full label if shown (e.g. "SPXW 7,725 / 7,735 Calls")
  direction    : "long" | "short" | null
  qty          : position size in CONTRACTS or lots (e.g. -5, 13, 15)
  expiry       : expiration date "YYYY-MM-DD" if shown
  credit       : average credit / fill price per contract (e.g. 7.25, 4.55)
  fill_price   : the fill or mark price if shown
  account      : "RH" | "Tradier" | "Funded" | "personal" | null (from any tag)
  pct          : the exact percentage shown (e.g. 14.61, 70) — the PRECISE number
  strikes      : array of strike prices shown [7725, 7735]
  structure    : "vertical" | "iron_fly" | "condor" | "single" | null
  risk         : the stated risk in dollars if shown
  reward       : the stated reward in dollars if shown
  pnl          : unrealized or realized P&L in dollars if shown
  message_on_image : ANY message/annotation painted or written on the image
                     (e.g. "7680 possible tonight", "bounce back", a target).
                     This is OFTEN the actual signal — capture it verbatim.
  chart_levels : any explicit price levels/lines shown (e.g. [7680, 7720])
  positions    : if the screen shows a LIST/TABLE of MULTIPLE positions (a
                 portfolio / positions screen), an array with ONE object per
                 visible row, e.g.:
                 {"instrument": "SPXW 7,745 / 7,755 Calls", "underlying": "SPXW",
                  "strikes": [7745, 7755], "expiry": "2026-10-29", "qty": 6,
                  "structure": "vertical", "pnl": 360.0}
                 Use the TOP-LEVEL fields for a single-position screen; use
                 positions[] for a multi-row screen — one entry per position,
                 every row you can see.
  scanner_rows : if the screen is a SCANNER TABLE (tickers with scores and
                 BUY/SELL per timeframe), an array with one object per row:
                 {"ticker": "AN", "score": 100, "verdict": "BUY"}
                 (score is the signed Net Score column; verdict is the 1H/4H
                 action when shown). One entry per visible row.

Return ONLY the JSON object, no commentary. If the image is a chart with a
message painted on it, message_on_image is the MOST important field."""


def _hermes_models(cfg_models):
    return list(cfg_models or [])


def extract_image_fields(photo: str, cfg: dict) -> dict:
    """Read a screenshot -> dict of visible fields (best-effort, never raises).
    Returns {} if the image can't be read or nothing is extracted."""
    p = Path(photo)
    if not p.exists():
        return {}
    QUERY_FILE.write_text(EXTRACT_PROMPT, encoding="utf-8")
    models = [cfg.get("hermes_model")] + list(cfg.get("hermes_fallback_models") or [])
    for model in models:
        if not model:
            continue
        cmd = ["cmd", "/c", cfg["hermes_bin"], "chat",
               "--query-file", str(QUERY_FILE), "-Q", "--in", str(HERE),
               "--provider", cfg["hermes_provider"], "-m", model,
               "--image", str(p)]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=cfg.get("hermes_timeout", 180),
                               cwd=str(HERE), errors="replace")
        except subprocess.TimeoutExpired:
            continue
        if r.returncode != 0:
            continue
        fields = _parse_json(r.stdout or "")
        if fields:
            return _normalise(fields)
    return {}


def _parse_json(out: str):
    m = re.search(r"\{.*\}", out or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _normalise(f: dict) -> dict:
    """Coerce the extracted fields to the types the Record expects."""
    out = {}
    def num(v):
        try:
            return float(v) if v not in (None, "", "null") else None
        except (TypeError, ValueError):
            return None
    if f.get("message_on_image"):
        out["message_on_image"] = str(f["message_on_image"])
    if isinstance(f.get("chart_levels"), list):
        cl = [num(x) for x in f["chart_levels"]]
        out["chart_levels"] = [x for x in cl if x is not None]
    for k in ("underlying", "instrument", "direction", "account", "structure"):
        if f.get(k):
            out[k] = str(f[k]).lower() if k in ("direction", "account", "structure") else str(f[k])
    for k in ("qty", "credit", "fill_price", "pct", "risk", "reward", "pnl"):
        v = num(f.get(k))
        if v is not None:
            out[k] = int(v) if k == "qty" else v
    # P&L SCALE GUARD: vision sometimes reads cents as dollars (msg 3729:
    # "$632.00" came back 63200). No single position on this channel is worth
    # >20k of P&L — anything larger is a scale slip, divide it back.
    if out.get("pnl") is not None and abs(out["pnl"]) > 20000:
        out["pnl"] = round(out["pnl"] / 100.0, 2)
    if isinstance(f.get("scanner_rows"), list):
        rows = []
        for r in f["scanner_rows"]:
            if not isinstance(r, dict):
                continue
            tk = str(r.get("ticker") or "").strip().upper()
            if not (2 <= len(tk) <= 5) or not tk.isalpha():
                continue
            sc = num(r.get("score"))
            rows.append({"ticker": tk, "score": int(sc) if sc is not None else None,
                         "verdict": str(r.get("verdict") or "").strip().upper()[:8]})
        if rows:
            out["scanner_rows"] = rows
    if f.get("expiry"):
        out["expiry"] = str(f["expiry"])
    if isinstance(f.get("strikes"), list):
        s = [num(x) for x in f["strikes"]]
        out["strikes"] = [x for x in s if x is not None]
    if isinstance(f.get("positions"), list):
        rows = []
        for r in f["positions"]:
            if not isinstance(r, dict):
                continue
            row = {}
            for k in ("instrument", "underlying", "structure", "expiry"):
                if r.get(k):
                    row[k] = str(r[k])
            v = num(r.get("qty"))
            if v is not None:
                row["qty"] = int(v)
            for k in ("pnl", "credit", "fill_price"):
                v = num(r.get(k))
                if v is not None:
                    if k == "pnl" and abs(v) > 20000:
                        v = round(v / 100.0, 2)      # scale guard (see above)
                    row[k] = v
            if isinstance(r.get("strikes"), list):
                s = [num(x) for x in r["strikes"]]
                row["strikes"] = [x for x in s if x is not None]
            if row.get("strikes"):
                rows.append(row)
        if rows:
            out["positions"] = rows
    return out


def merge_into_record(rec, fields: dict):
    """Fill Record gaps from screenshot fields. Screenshot wins for the fields it
    carries (qty/fill/account/pct are image-only), text wins for structure/strikes
    it already derived. Marks what came from the image."""
    if not fields:
        rec.missing.append("image_unread")
        return rec
    img = []
    if fields.get("qty") and not rec.qty:
        rec.qty = fields["qty"]; img.append("qty")
    if fields.get("credit") and not rec.credit:
        rec.credit = fields["credit"]; img.append("credit")
    if fields.get("expiry") and not rec.expiry:
        rec.expiry = fields["expiry"]; img.append("expiry")
    if fields.get("account") and not rec.account:
        rec.account = fields["account"]; img.append("account")
    if fields.get("pct") is not None:
        rec.pct_actual = fields["pct"]      # L11: the PRECISE pct is image-only
        img.append("pct")
    if fields.get("pnl") is not None and rec.pct_actual is None:
        rec.pct_actual = fields["pnl"]
    if fields.get("underlying") and not rec.underlying:
        rec.underlying = fields["underlying"]
    if fields.get("structure") and not rec.structure_type:
        rec.structure_type = fields["structure"]
    if fields.get("strikes") and not rec.legs:
        s = sorted(fields["strikes"])
        rec.body = s[len(s) // 2] if len(s) >= 3 else (s[0] if s else None)
        rec.wing_low = s[0] if s else None
        rec.wing_high = s[-1] if s else None
    if img:
        rec.missing = [m for m in rec.missing if m not in img]
        rec.source = "screenshot"
    return rec
