"""Email the brief (HTML body + Markdown fallback). Uses SMTP_* env vars, e.g. a Gmail app password."""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

log = logging.getLogger(__name__)


def send_email(cfg: dict, subject: str, html: str, markdown: str) -> None:
    em = cfg.get("email") or {}
    to = em.get("to") or os.environ.get("BRIEF_EMAIL_TO")
    host, user, pw = (os.environ.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"))
    if not (em.get("enabled") and to and host and user and pw):
        return
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.set_content(markdown)
    msg.add_alternative(html, subtype="html")
    try:
        with smtplib.SMTP_SSL(host, int(os.environ.get("SMTP_PORT", 465)), timeout=30) as s:
            s.login(user, pw)
            s.send_message(msg)
        log.info("emailed brief to %s", to)
    except Exception as e:
        log.warning("email failed: %s", e)
