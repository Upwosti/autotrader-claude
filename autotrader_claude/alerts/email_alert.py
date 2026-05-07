"""
Email alert sender — uses smtplib with Gmail/SMTP.
Silently skips if not configured.
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from loguru import logger

from config import (
    SMTP_HOST as EMAIL_SMTP_HOST,
    SMTP_PORT as EMAIL_SMTP_PORT,
    EMAIL_SENDER as EMAIL_ADDRESS,
    EMAIL_PASSWORD,
    EMAIL_RECEIVER as EMAIL_RECIPIENT,
)


class EmailAlert:
    """Sends email alerts via SMTP."""

    def __init__(self):
        self.enabled = bool(EMAIL_ADDRESS and EMAIL_PASSWORD and EMAIL_RECIPIENT)
        if not self.enabled:
            logger.debug("EmailAlert disabled — credentials not set")

    def send(self, subject: str, body: str) -> bool:
        """Send an email. Returns True on success."""
        if not self.enabled:
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_ADDRESS
        msg["To"] = EMAIL_RECIPIENT
        msg.attach(MIMEText(body, "plain"))

        try:
            with smtplib.SMTP(EMAIL_SMTP_HOST, EMAIL_SMTP_PORT, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
                server.sendmail(EMAIL_ADDRESS, EMAIL_RECIPIENT, msg.as_string())
            logger.debug(f"Email sent: {subject}")
            return True
        except Exception as e:
            logger.error(f"Email send failed: {e}")
            return False
