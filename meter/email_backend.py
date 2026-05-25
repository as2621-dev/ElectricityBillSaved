"""IMAP backend: pull SMT 'Subscription Report' emails and save their CSV attachments.

Connection contract (see `reference/integrations.md` section 1):
- SMT delivers a daily email to the account-profile inbox with the 15-min interval
  report **attached** (confirmed: `text/csv`; SMT sometimes ZIP-wraps, handled here).
- Auth is plain IMAP login. For Gmail this REQUIRES a 16-char **app password**
  (not the account password, and not OAuth) with 2-Step Verification enabled.
- We never delete mail. Idempotency is by attachment filename on disk — SMT
  filenames embed a unique report id, and re-deliveries reuse the same name.

This file only fetches/saves. Parsing the CSV into intervals is `csv_parser.py`.
"""

from __future__ import annotations

import email
import imaplib
import io
import logging
import os
import sys
import zipfile
from datetime import date, datetime, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path

import structlog

from .models import SavedReport

logger = structlog.get_logger()

# Gmail IMAP defaults; overridable via env so Fastmail/etc. also work.
DEFAULT_IMAP_HOST: str = "imap.gmail.com"
DEFAULT_IMAP_PORT: int = 993
DEFAULT_MAILBOX: str = "INBOX"
# The real sender observed on live mail. A substring match keeps it robust to
# display-name formatting differences across IMAP servers.
DEFAULT_FROM_FILTER: str = "smartmetertexas.com"

_CSV_SUFFIXES: tuple[str, ...] = (".csv",)
_ZIP_SUFFIXES: tuple[str, ...] = (".zip",)


def configure_logging() -> None:
    """Route structured logs to stderr so CLI stdout stays clean for results."""
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


def _imap_since(since: date) -> str:
    """Format a date for an IMAP SINCE search term (e.g. '19-May-2026')."""
    return since.strftime("%d-%b-%Y")


def _received_at(msg: Message) -> datetime:
    """Best-effort parse of the email Date header to an aware datetime (UTC fallback)."""
    raw = msg.get("Date")
    if raw:
        try:
            return parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            pass
    return datetime.now(tz=timezone.utc)


def _iter_csv_payloads(msg: Message):
    """Yield (filename, csv_bytes, was_zip) for every CSV attachment in the message.

    Handles a plain `.csv` attachment and a `.zip` that wraps one or more CSVs.
    """
    for part in msg.walk():
        filename = part.get_filename()
        if not filename:
            continue
        lower = filename.lower()
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        if lower.endswith(_CSV_SUFFIXES):
            yield filename, payload, False
        elif lower.endswith(_ZIP_SUFFIXES):
            try:
                with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                    for member in archive.namelist():
                        if member.lower().endswith(_CSV_SUFFIXES):
                            yield Path(member).name, archive.read(member), True
            except zipfile.BadZipFile:
                logger.error(
                    "smt_zip_unreadable",
                    attachment_filename=filename,
                    fix_suggestion="Attachment claimed .zip but did not parse; inspect it under data/quarantine/.",
                )


def fetch_reports(
    *,
    username: str,
    app_password: str,
    host: str = DEFAULT_IMAP_HOST,
    port: int = DEFAULT_IMAP_PORT,
    mailbox: str = DEFAULT_MAILBOX,
    from_filter: str = DEFAULT_FROM_FILTER,
    dest_dir: str | os.PathLike[str] = "data/smt",
    since: date | None = None,
    unseen_only: bool = False,
) -> list[SavedReport]:
    """Fetch SMT report emails over IMAP and save their CSV attachments to disk.

    Args:
        username: IMAP login (the full email address).
        app_password: Gmail app password (NOT the account password).
        host: IMAP server hostname.
        port: IMAP SSL port (993 for Gmail).
        mailbox: Folder/label to search (default INBOX).
        from_filter: Substring of the sender address to match (SMT sender domain).
        dest_dir: Directory to write CSVs into (created if missing).
        since: Only fetch mail on/after this date (IMAP SINCE). None = no lower bound.
        unseen_only: If True, restrict to UNSEEN messages.

    Returns:
        A list of `SavedReport`, one per CSV written this run (skips files already on disk).

    Raises:
        imaplib.IMAP4.error: On login or mailbox-select failure.

    Example:
        >>> reports = fetch_reports(username="you@gmail.com", app_password="xxxx")
        >>> reports[0].attachment_filename
        'IntervalMeterUsage….CSV'
    """
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)

    logger.info(
        "smt_fetch_started",
        host=host,
        mailbox=mailbox,
        from_filter=from_filter,
        since=since.isoformat() if since else None,
        unseen_only=unseen_only,
    )

    criteria: list[str] = ["FROM", from_filter]
    if since is not None:
        criteria += ["SINCE", _imap_since(since)]
    if unseen_only:
        criteria.append("UNSEEN")

    saved: list[SavedReport] = []
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
        logger.info("smt_messages_matched", count=len(message_ids))

        for raw_id in message_ids:
            status, msg_data = connection.fetch(raw_id, "(RFC822)")
            if status != "OK" or not msg_data or msg_data[0] is None:
                logger.error(
                    "smt_message_fetch_failed",
                    message_id=raw_id.decode(errors="replace"),
                    fix_suggestion="Re-run; the IMAP fetch returned no body for this id.",
                )
                continue

            msg = email.message_from_bytes(msg_data[0][1])
            sender = msg.get("From", "")
            subject = str(make_header(decode_header(msg.get("Subject", ""))))
            received_at = _received_at(msg)

            for attachment_filename, payload, was_zip in _iter_csv_payloads(msg):
                out_path = dest / attachment_filename
                if out_path.exists():
                    logger.info("smt_attachment_skipped_exists", attachment_filename=attachment_filename)
                    continue
                out_path.write_bytes(payload)
                saved.append(
                    SavedReport(
                        message_id=raw_id.decode(errors="replace"),
                        sender=sender,
                        subject=subject,
                        received_at=received_at,
                        attachment_filename=attachment_filename,
                        saved_path=str(out_path.resolve()),
                        was_zip=was_zip,
                    )
                )
                logger.info(
                    "smt_attachment_saved",
                    attachment_filename=attachment_filename,
                    saved_path=str(out_path),
                    was_zip=was_zip,
                )
    finally:
        try:
            connection.logout()
        except Exception:  # noqa: BLE001 - logout best-effort; nothing to recover
            pass

    logger.info("smt_fetch_completed", saved_count=len(saved))
    return saved
