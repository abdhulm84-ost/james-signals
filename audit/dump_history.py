#!/usr/bin/env python3
"""dump_history.py — one-off: dump EVERY message in the James channel to
audit/messages_dump.jsonl (chronological) for the backfill audit."""

import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from james_pipe import ENV_PATH, load_env  # noqa: E402

CHANNEL = "James Private Signal Inbox"
OUT = HERE / "messages_dump.jsonl"

from telethon import TelegramClient  # noqa: E402


async def main():
    env = load_env()
    client = TelegramClient(str(HERE / "telegram_audit.session"),
                            int(env["TELEGRAM_API_ID"]), env["TELEGRAM_API_HASH"])
    await client.connect()
    if not await client.is_user_authorized():
        print("not authorized")
        return
    entity = await client.get_entity(CHANNEL)
    rows = []
    async for m in client.iter_messages(entity, limit=int(sys.argv[1]) if len(sys.argv) > 1 else None):
        rows.append({
            "id": m.id,
            "date": m.date.isoformat() if m.date else None,
            "text": m.message or "",
            "photo": bool(m.photo),
            "grouped_id": str(m.grouped_id) if m.grouped_id else None,
            "edited": m.edit_date.isoformat() if m.edit_date else None,
            "views": m.views,
        })
    rows.reverse()
    OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    print(f"dumped {len(rows)} messages -> {OUT}")
    await client.disconnect()

asyncio.run(main())
