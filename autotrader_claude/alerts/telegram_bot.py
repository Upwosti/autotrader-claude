"""
Telegram alert sender — sends messages via Bot API using requests.
Silently skips if not configured.
"""

import requests
from loguru import logger

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


class TelegramAlert:
    """Sends Telegram messages via the Bot HTTP API."""

    BASE_URL = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self):
        self.enabled = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
        if not self.enabled:
            logger.debug("TelegramAlert disabled — TOKEN/CHAT_ID not set")

    def send(self, subject: str, body: str) -> bool:
        """Send a message. Returns True on success."""
        if not self.enabled:
            return False

        text = f"*{self._escape(subject)}*\n\n{self._escape(body)}"
        url = self.BASE_URL.format(token=TELEGRAM_BOT_TOKEN)
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "MarkdownV2",
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                logger.debug(f"Telegram sent: {subject}")
                return True
            logger.warning(f"Telegram error {resp.status_code}: {resp.text[:200]}")
            return False
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    def _escape(self, text: str) -> str:
        """Escape special chars for MarkdownV2."""
        special = r"\_*[]()~`>#+-=|{}.!"
        for ch in special:
            text = text.replace(ch, f"\\{ch}")
        return text
