"""IMAP fetch + parse of the 4Change 'Your bill's ready' notification email.

Yields a `BillNotice` (amount due, due date, billing period) per notice. We read
ONLY the notifications sender; the marketing sender (Info@mail.4changeenergy.com)
sends look-alike "your ... win!" mail that a naive from:4changeenergy.com filter
would wrongly match. The email gives the dollar amount and period; kWh and the
credit are computed by us — we never scrape the portal or fetch the PDF.
"""

from __future__ import annotations

import email
import imaplib
import re
from datetime import date, datetime, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime

import structlog

from .models import BillNotice

logger = structlog.get_logger()

DEFAULT_IMAP_HOST: str = "imap.gmail.com"
DEFAULT_IMAP_PORT: int = 993
DEFAULT_MAILBOX: str = "INBOX"
# The transactional sender (NOT the marketing sender Info@mail.4changeenergy.com).
DEFAULT_FROM_FILTER: str = "4change@notifications.4changeenergy.com"

_ACCOUNT_RE = re.compile(r"Account\s*Number:\s*(\d+)", re.IGNORECASE)
_AMOUNT_RE = re.compile(r"Amount\s*Due:\s*\$?\s*([\d,]+\.\d{2})", re.IGNORECASE)
_DUE_RE = re.compile(r"Due\s*Date:\s*(\d{1,2}/\d{1,2}/\d{4})", re.IGNORECASE)
_PERIOD_RE = re.compile(
    r"Billing\s*Period:\s*(\d{1,2}/\d{1,2}/\d{4})\s*[-–]\s*(\d{1,2}/\d{1,2}/\d{4})",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _plaintext_body(msg: Message) -> str:
    """Extract a plaintext view of the email, stripping HTML tags if that's all there is."""
    plains: list[str] = []
    htmls: list[str] = []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ctype = part.get_content_type()
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        text = payload.decode(part.get_content_charset() or "utf-8", "replace")
        if ctype == "text/plain":
            plains.append(text)
        elif ctype == "text/html":
            htmls.append(_TAG_RE.sub(" ", text))
    return "\n".join(plains) if plains else "\n".join(htmls)


def _parse_us_date(value: str) -> date:
    """Parse an MM/DD/YYYY string into a civil date."""
    return datetime.strptime(value, "%m/%d/%Y").date()


def parse_bill_notice(message_id: str, msg: Message) -> BillNotice | None:
    """Parse one notification email into a `BillNotice`, or None if fields are missing.

    Args:
        message_id: IMAP message id (idempotency key).
        msg: The parsed email message.

    Returns:
        A `BillNotice`, or None if the body lacks the required fields (logged).
    """
    body = _plaintext_body(msg)
    amount = _AMOUNT_RE.search(body)
    due = _DUE_RE.search(body)
    period = _PERIOD_RE.search(body)
    account = _ACCOUNT_RE.search(body)
    if not (amount and due and period and account):
        logger.error(
            "bill_notice_unparseable",
            message_id=message_id,
            subject=str(make_header(decode_header(msg.get("Subject", "")))),
            found={"amount": bool(amount), "due": bool(due), "period": bool(period), "account": bool(account)},
            fix_suggestion="Confirm this is a 4Change 'Your bill's ready' email; the body layout may have changed.",
        )
        return None

    raw_date = msg.get("Date")
    received_at = parsedate_to_datetime(raw_date) if raw_date else datetime.now(tz=timezone.utc)
    return BillNotice(
        account_number=account.group(1),
        amount_due_cents=round(float(amount.group(1).replace(",", "")) * 100),
        due_date=_parse_us_date(due.group(1)),
        period_start=_parse_us_date(period.group(1)),
        period_end=_parse_us_date(period.group(2)),
        email_message_id=message_id,
        received_at=received_at,
    )


def fetch_bill_notices(
    *,
    username: str,
    app_password: str,
    host: str = DEFAULT_IMAP_HOST,
    port: int = DEFAULT_IMAP_PORT,
    mailbox: str = DEFAULT_MAILBOX,
    from_filter: str = DEFAULT_FROM_FILTER,
    since: date | None = None,
) -> list[BillNotice]:
    """Fetch and parse 4Change bill-ready notices over IMAP.

    Args:
        username: IMAP login (full email address).
        app_password: Gmail app password (not the account password).
        host: IMAP server hostname.
        port: IMAP SSL port.
        mailbox: Folder/label to search.
        from_filter: Transactional sender to match (excludes the marketing sender).
        since: Only mail on/after this date (IMAP SINCE), or None for no bound.

    Returns:
        Parsed `BillNotice` objects, newest-first as IMAP returns them.

    Raises:
        imaplib.IMAP4.error: On login or mailbox-select failure.
    """
    criteria: list[str] = ["FROM", from_filter, "SUBJECT", "bill"]
    if since is not None:
        criteria += ["SINCE", since.strftime("%d-%b-%Y")]

    logger.info("bill_notice_fetch_started", host=host, mailbox=mailbox, from_filter=from_filter)
    notices: list[BillNotice] = []
    connection = imaplib.IMAP4_SSL(host, port)
    try:
        connection.login(username, app_password)
        status, _ = connection.select(mailbox, readonly=True)
        if status != "OK":
            raise imaplib.IMAP4.error(f"could not select mailbox {mailbox!r}")
        status, data = connection.search(None, *criteria)
        if status != "OK":
            raise imaplib.IMAP4.error(f"IMAP search failed: {criteria!r}")

        message_ids = data[0].split()
        logger.info("bill_notice_messages_matched", count=len(message_ids))
        for raw_id in message_ids:
            status, msg_data = connection.fetch(raw_id, "(RFC822)")
            if status != "OK" or not msg_data or msg_data[0] is None:
                continue
            notice = parse_bill_notice(raw_id.decode(errors="replace"), email.message_from_bytes(msg_data[0][1]))
            if notice is not None:
                notices.append(notice)
    finally:
        try:
            connection.logout()
        except Exception:  # noqa: BLE001 - logout best-effort
            pass

    logger.info("bill_notice_fetch_completed", parsed_count=len(notices))
    return notices
