import os
import base64
import json
from pathlib import Path
import anthropic

# Read key
env_path = Path("F:/Projects on Trading/Telegram/.env")
key = None
with open(env_path, "r", encoding="utf-8") as f:
    for line in f:
        if line.startswith("ANTHROPIC_API_KEY="):
            key = line.split("=", 1)[1].strip()
            break

if not key:
    print("Error: ANTHROPIC_API_KEY not found in env file")
    exit(1)

client = anthropic.Anthropic(api_key=key)

image_path = "C:/Trading/james-signals/photos/4130.jpg"
with open(image_path, "rb") as image_file:
    image_data = base64.b64encode(image_file.read()).decode("utf-8")

prompt = """You are a trading-screenshot reader. Look at the attached image
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

Return ONLY the JSON object, no commentary, no markdown code block formatting (i.e. do not wrap in ```json ... ```). Keep it raw JSON. If the image is a chart with a
message painted on it, message_on_image is the MOST important field."""

try:
    message = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=1500,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ],
            }
        ],
    )
    print(message.content[0].text)
except Exception as e:
    print(f"Error calling Anthropic API: {e}")
