"""Telegram message to user when a page needs a person (bot check, login), so user knows to look at
the PC even when away from the chat. Set up once outside chat: data/telegram.json with
{"token": "<bot token>", "chat_id": "<user's chat id>"} (or env TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).
Without them nothing is sent. One message per site per 10 minutes."""

import asyncio
import json
import os
import time
import urllib.parse
import urllib.request
from typing import Dict, Optional

from scrapling.webreader.browser import ROOT

CONFIG = ROOT / "data" / "telegram.json"
QUIET_S = 600
_last: Dict[str, float] = {}


def _config() -> Optional[Dict[str, str]]:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        try:
            d = json.loads(CONFIG.read_text(encoding="utf-8"))
            token, chat = d.get("token"), d.get("chat_id")
        except (OSError, ValueError):
            return None
    return {"token": token, "chat_id": str(chat)} if token and chat else None


def configured() -> bool:
    return _config() is not None


def _send(cfg: Dict[str, str], text: str) -> bool:
    data = urllib.parse.urlencode({"chat_id": cfg["chat_id"], "text": text}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{cfg['token']}/sendMessage", data=data)
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status == 200


async def needs_human(site: str, reason: str, url: str) -> Optional[str]:
    """Returns "sent", "quiet" (sent recently for this site), "not_configured" or "failed"."""
    cfg = _config()
    if not cfg:
        return "not_configured"
    if time.time() - _last.get(site, 0) < QUIET_S:
        return "quiet"
    _last[site] = time.time()
    text = f"[Claude] {site} 확인 필요({reason}). PC의 브라우저 창에서 풀어 주세요.\n{url[:200]}"
    try:
        return "sent" if await asyncio.to_thread(_send, cfg, text) else "failed"
    except Exception:
        return "failed"
