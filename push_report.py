#!/usr/bin/env python3
"""push_report.py <md-file> — one-shot Saved Messages push of a report file."""
import pathlib, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import james_pipe as jp
text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").strip()
st = {"alerts_today": 0, "alerts_date": jp.today(), "push_recent": {}}
jp.push(text, st)
time.sleep(8)          # let _push_send's thread finish
print("pushed:", len(text), "chars")
