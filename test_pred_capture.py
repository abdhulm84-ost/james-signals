"""Pins the 2026-11-08 prediction-capture fixes: a non-ticker word (FOMC) must
not become the underlying, and a verbatim same-day re-post must not file twice."""
import csv
import james_pipe as jp

jp.PRED_FILE = jp.HERE / "test_pred_tmp.csv"
if jp.PRED_FILE.exists():
    jp.PRED_FILE.unlink()

_orig = jp._under_quote
jp._under_quote = lambda s: {"SPX": "7376.86", "SPX500": "7376.86"}.get(s)

TXT = ("We are holding Bear calls from yesterday. Post FOMC, we will see a big "
       "drop today")
jp.record_prediction({"instrument": "SPX500"}, 9001, TXT)
jp.record_prediction({"instrument": "SPX500"}, 9002, TXT)   # verbatim re-post
jp.record_prediction({"instrument": "SPX500"}, 9003, "NYFP1 T5 down, expect a drop")

rows = list(csv.DictReader(jp.PRED_FILE.open(encoding="utf-8")))
assert len(rows) == 2, f"dedupe failed: {len(rows)} rows"
assert rows[0]["level"].startswith("SPX@"), f"FOMC leaked into level: {rows[0]['level']}"
assert rows[0]["level"] == "SPX@7376.86", rows[0]["level"]
assert rows[1]["level"].startswith("SPX@"), f"NYFP1 leaked: {rows[1]['level']}"
assert all(r["result"] == "open" for r in rows)

jp._under_quote = lambda s: None
jp.record_prediction({"instrument": "SPX500"}, 9004, "will bounce hard here")
rows = list(csv.DictReader(jp.PRED_FILE.open(encoding="utf-8")))
assert rows[-1]["level"] == "SPX@", f"No-quote fallback wrong: {rows[-1]['level']}"
jp._under_quote = _orig
jp.PRED_FILE.unlink()
print("test_pred_capture 6/6 ok")
