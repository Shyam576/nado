"""
modules/email_watcher.py — IMAP inbox polling, broadcast as new-email alerts.

Polled periodically by bot/telegram_bot.py's job queue; each new message
found is broadcast cross-platform via bot.notifier.broadcast() (Discord +
Telegram) — same one-shot-alert pattern as finance.check_price_target() /
check_ter_targets(). Read-only IMAP (BODY.PEEK) so polling never marks mail
as read.
"""

import email.header
import email.message
import email.utils
import html
import imaplib
import logging
import re

import memory
from config import EMAIL_ADDRESS, EMAIL_FOLDER, EMAIL_IMAP_HOST, EMAIL_IMAP_PORT, EMAIL_PASSWORD

logger = logging.getLogger(__name__)

_LAST_UID_KEY = "email_last_uid"
_UIDVALIDITY_KEY = "email_uidvalidity"
_MAX_ALERTS_PER_POLL = 10  # guards against flooding chat with a huge backlog on first connect
_MAX_BODY_PREVIEW_CHARS = 1200  # keeps sender+subject+body comfortably under Discord's 2000-char limit
_MAX_ALERT_CHARS = 1900


def _decode_header_value(raw: str | None) -> str:
    """Decode a MIME-encoded header (e.g. '=?UTF-8?B?...?=') into plain text."""
    if not raw:
        return "(unknown)"
    parts = email.header.decode_header(raw)
    decoded = "".join(
        part.decode(encoding or "utf-8", errors="replace") if isinstance(part, bytes) else part
        for part, encoding in parts
    )
    return decoded.strip() or "(unknown)"


def _extract_body_preview(msg: email.message.Message) -> str:
    """Best-effort plain-text preview of a message body, truncated for chat delivery.

    Prefers a text/plain part; falls back to text/html with tags stripped if
    that's all the message has. Attachments are skipped either way.
    """
    plain_part = None
    html_part = None

    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.get_content_disposition() == "attachment":
            continue
        content_type = part.get_content_type()
        if content_type == "text/plain" and plain_part is None:
            plain_part = part
        elif content_type == "text/html" and html_part is None:
            html_part = part

    part = plain_part or html_part
    if part is None:
        return "(no text content)"

    try:
        payload = part.get_payload(decode=True)
        charset = part.get_content_charset() or "utf-8"
        text = payload.decode(charset, errors="replace") if payload else ""
    except (LookupError, ValueError) as exc:
        logger.warning("Could not decode email body: %s", exc)
        return "(could not decode message body)"

    if part is html_part:
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    if not text:
        return "(empty message)"
    if len(text) > _MAX_BODY_PREVIEW_CHARS:
        text = text[:_MAX_BODY_PREVIEW_CHARS].rstrip() + "…"
    return text


def check_new_emails() -> list[str]:
    """Poll the configured IMAP inbox for messages that arrived since the last check.

    Returns:
        A list of alert strings (one per new email), oldest first. Empty if
        email watching isn't configured, nothing new arrived, or the poll
        failed (errors are logged, not raised, so one bad poll doesn't kill
        the caller's job loop).
    """
    if not (EMAIL_ADDRESS and EMAIL_PASSWORD and EMAIL_IMAP_HOST):
        return []

    conn = None
    try:
        conn = imaplib.IMAP4_SSL(EMAIL_IMAP_HOST, EMAIL_IMAP_PORT)
        conn.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        typ, _ = conn.select(EMAIL_FOLDER, readonly=True)
        if typ != "OK":
            logger.error("Could not select IMAP folder %r", EMAIL_FOLDER)
            return []

        uidvalidity_resp = conn.response("UIDVALIDITY")[1][0]
        uidvalidity = uidvalidity_resp.decode() if isinstance(uidvalidity_resp, bytes) else uidvalidity_resp

        # "ALL" rather than a "UID last_uid+1:*" range search — IMAP treats a
        # start-UID past the mailbox's current max as wrapping to "*:start",
        # which would re-return the newest message as if it were new. Any
        # inbox size this polls against is cheap to list in full.
        typ, data = conn.uid("search", None, "ALL")
        if typ != "OK" or not data or not data[0]:
            return []
        all_uids = [int(u) for u in data[0].split()]
        if not all_uids:
            return []

        stored_uidvalidity = memory.get_preference(_UIDVALIDITY_KEY)
        last_uid = memory.get_preference(_LAST_UID_KEY)

        # First run, or the mailbox got reset (UIDVALIDITY changed) — baseline
        # to the current newest message instead of alerting the whole inbox.
        if stored_uidvalidity != uidvalidity or last_uid is None:
            memory.set_preference(_UIDVALIDITY_KEY, uidvalidity)
            memory.set_preference(_LAST_UID_KEY, max(all_uids))
            return []

        new_uids = sorted(u for u in all_uids if u > int(last_uid))
        if not new_uids:
            return []

        alerts: list[str] = []
        for uid in new_uids[:_MAX_ALERTS_PER_POLL]:
            typ, msg_data = conn.uid("fetch", str(uid), "(BODY.PEEK[])")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            raw_bytes = msg_data[0][1]
            msg = email.message_from_bytes(raw_bytes)
            sender_name, sender_addr = email.utils.parseaddr(msg.get("From", ""))
            sender = _decode_header_value(sender_name) if sender_name else (sender_addr or "(unknown)")
            subject = _decode_header_value(msg.get("Subject"))
            body_preview = _extract_body_preview(msg)

            alert = f"\U0001F4E7 New email from {sender}\nSubject: {subject}\n\n{body_preview}"
            if len(alert) > _MAX_ALERT_CHARS:
                alert = alert[:_MAX_ALERT_CHARS].rstrip() + "…"
            alerts.append(alert)

        memory.set_preference(_LAST_UID_KEY, max(new_uids))
        if len(new_uids) > _MAX_ALERTS_PER_POLL:
            logger.warning(
                "Email watcher: %d new messages, only alerting the first %d",
                len(new_uids), _MAX_ALERTS_PER_POLL,
            )
        return alerts
    except Exception as exc:  # noqa: BLE001
        logger.exception("Email watcher poll failed: %s", exc)
        return []
    finally:
        if conn is not None:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass
