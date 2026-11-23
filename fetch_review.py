#!/usr/bin/env python3
"""fetch_review.py — read channel messages WITHOUT touching the live pipe's session.

The live pipe uses telegram_watcher.session. Running telethon against that same
file from review code locks the SQLite DB and breaks the pipe's live listener
(messages get missed). This uses a SEPARATE copy (telegram_review.session) so
review reads never interfere with the live watcher.

Rule: ALWAYS use this for message reads while the pipe is running. Never import
and connect against telegram_watcher.session directly.
"""
import sys
import james_pipe as jp
from telethon.sync import TelegramClient

def fetch(limit=30, min_id=None):
    env = jp.load_env()
    client = TelegramClient(str(jp.HERE / "telegram_review.session"),
                            int(env["TELEGRAM_API_ID"]), env["TELEGRAM_API_HASH"])
    client.connect()
    ch = client.get_entity("James Private Signal Inbox")
    kw = {"limit": limit}
    if min_id:
        kw = {"min_id": min_id}
    msgs = sorted(client.iter_messages(ch, **kw), key=lambda m: m.id)
    client.disconnect()
    return [{"id": m.id, "date": str(m.date), "photo": bool(m.photo),
             "text": (m.message or "<IMAGE ONLY>")} for m in msgs]

if __name__ == "__main__":
    for m in fetch(limit=int(sys.argv[1]) if len(sys.argv) > 1 else 30):
        tag = "IMG" if m["photo"] else "txt"
        print(f"[{m['id']} {m['date'][5:16]} {tag}] {m['text'][:130]}")
