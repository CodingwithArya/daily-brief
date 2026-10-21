"""Email the brief (HTML body + Markdown fallback). Uses SMTP_* env vars, e.g. a Gmail app password."""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

log = logging.getLogger(__name__)


def send_email(cfg: dict, subject: str, html: str, markdown: str) -> None:
    em = cfg.get("email") or {}
    to = (em.get("to") or os.environ.get("BRIEF_EMAIL_TO") or "").strip()
    host, user, pw = ((os.environ.get(k) or "").strip() for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"))
    pw = pw.replace(" ", "")  # Gmail app passwords are shown with spaces
    if not em.get("enabled"):
        log.info("email: skipped — email.enabled is false in config.yaml")
        return
    missing = [n for n, v in [("BRIEF_EMAIL_TO", to), ("SMTP_HOST", host), ("SMTP_USER", user), ("SMTP_PASS", pw)] if not v]
    if missing:
        log.warning("email: skipped — missing secret(s): %s", ", ".join(missing))
        return
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.set_content(markdown)
    msg.add_alternative(html, subtype="html")
    try:
        with smtplib.SMTP_SSL(host, int(os.environ.get("SMTP_PORT") or 465), timeout=30) as s:
            s.login(user, pw)
            s.send_message(msg)
        log.info("email: sent brief to %s", to)
    except smtplib.SMTPAuthenticationError:
        log.error("email failed: Gmail rejected the login — SMTP_USER must be your full Gmail address and "
                  "SMTP_PASS a 16-character app password (not your normal password)")
    except Exception as e:
        log.error("email failed: %s", e)
