"""Tracking agent: reads your inbox over IMAP and updates application status.

Rules (regex) classify each email as applied / viewed / shortlisted / interview /
offer / rejected, then the email is linked to an application by company name and
job title. Emails are opened read-only, so they stay unread in your inbox.
"""

from __future__ import annotations

import email
import imaplib
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime

from .. import db
from ..config import Settings
from ..matching import clean_text

log = logging.getLogger(__name__)

# Checked in this order; the first match wins.
RULES: list[tuple[str, re.Pattern[str]]] = [
    ("rejected", re.compile(
        r"unfortunately|not (be )?moving forward|move forward with other|decided to (pursue|proceed with) other"
        r"|not been selected|were not selected|will not be (proceeding|progressing)|regret to inform"
        r"|position has been filled|not a (fit|match) at this time|other candidates whose|no longer under consideration"
        r"|decided not to proceed|unable to (move forward|offer you)", re.I)),
    ("offer", re.compile(r"offer letter|pleased to offer|extend (you )?an offer|job offer|offer of employment", re.I)),
    ("interview", re.compile(
        r"\binterview\b|schedule (a|your) (call|chat|meeting)|invite you to|next round|technical round"
        r"|coding (test|challenge|assessment)|online assessment|take-home|hackerrank|codility|calendly\.com", re.I)),
    ("shortlisted", re.compile(
        r"short-?listed|moved (you )?to the next (stage|step)|selected for the next|move forward with your application"
        r"|like to (learn more|know more|connect)|recruiter (is interested|wants to)|interested in your profile", re.I)),
    ("viewed", re.compile(r"(application|profile) (was|has been) viewed|viewed your (application|profile)|recruiter viewed", re.I)),
    ("applied", re.compile(
        r"application was sent|thank(s| you) for (applying|your application|your interest)|received your application"
        r"|application (has been )?(submitted|received)|successfully applied", re.I)),
]

COMPANY_SUFFIXES = re.compile(
    r"\b(private|pvt|ltd|limited|inc|llc|llp|corp|corporation|technologies|technology|tech|labs|solutions|india|software|co)\b\.?",
    re.I,
)


def classify(subject: str, body: str) -> str | None:
    text = f"{subject}\n{body[:6000]}"
    for status, pattern in RULES:
        if pattern.search(text):
            return status
    return None


def _norm_company(name: str) -> str:
    name = COMPANY_SUFFIXES.sub(" ", name.lower())
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", name).split())


def find_job(conn: sqlite3.Connection, subject: str, sender: str, body: str) -> sqlite3.Row | None:
    haystack = f"{subject} {sender} {body[:8000]}".lower()
    candidates = conn.execute(
        "SELECT * FROM jobs WHERE status IN ('applied','viewed','shortlisted','interview','needs_attention') "
        "ORDER BY applied_at DESC"
    ).fetchall()
    best: tuple[int, sqlite3.Row] | None = None
    for row in candidates:
        company = _norm_company(row["company"] or "")
        if len(company) < 3:
            continue
        if not re.search(r"(?<![a-z0-9])" + re.escape(company) + r"(?![a-z0-9])", haystack):
            continue
        score = 10
        title = (row["title"] or "").lower()
        if title and title in haystack:
            score += 10
        if row["status"] == "needs_attention":
            score -= 5
        if best is None or score > best[0]:
            best = (score, row)
    return best[1] if best else None


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _body(msg: Message) -> str:
    plain, html = "", ""
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        try:
            payload = part.get_payload(decode=True)
            if not isinstance(payload, bytes):
                continue
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            continue
        if part.get_content_type() == "text/plain" and not plain:
            plain = text
        elif part.get_content_type() == "text/html" and not html:
            html = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    return clean_text(plain or html)


def process_message(conn: sqlite3.Connection, message_id: str, subject: str, sender: str, body: str, received: str) -> str | None:
    """Classify one email and update the matching job. Returns the new status, if any."""
    if conn.execute("SELECT 1 FROM processed_emails WHERE message_id = ?", (message_id,)).fetchone():
        return None
    status = classify(subject, body)
    job = find_job(conn, subject, sender, body) if status else None
    updated = None
    if job is not None and status:
        current = job["status"]
        if current == "needs_attention" and status in db.TRACKING_RANK:
            # You finished it by hand; the confirmation email proves it.
            db.set_status(conn, job["id"], "applied", f"Email: {subject[:120]}")
            current = "applied"
            updated = "applied"
        if db.is_forward_move(current, status):
            db.set_status(conn, job["id"], status, f"Email: {subject[:120]}")
            updated = status
    conn.execute(
        "INSERT OR IGNORE INTO processed_emails (message_id, job_id, classification, subject, sender, received_at, processed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (message_id, job["id"] if job is not None else None, status, subject[:300], sender[:200], received, db.now_iso()),
    )
    conn.commit()
    return updated


def run_tracker(settings: Settings) -> dict[str, int]:
    cfg = settings.email
    if not cfg.get("enabled"):
        log.info("Email tracking disabled in config.")
        return {}
    password = os.environ.get(cfg.get("password_env", "JOBAGENT_EMAIL_PASSWORD"), "")
    if not password:
        raise RuntimeError(f"Set the {cfg.get('password_env', 'JOBAGENT_EMAIL_PASSWORD')} environment variable (an app password).")

    conn = db.connect()
    stats: dict[str, int] = {"scanned": 0, "updated": 0}
    since = (datetime.now() - timedelta(days=int(cfg.get("lookback_days", 14)))).strftime("%d-%b-%Y")

    imap = imaplib.IMAP4_SSL(cfg.get("imap_host", "imap.gmail.com"))
    try:
        imap.login(cfg["username"], password)
        imap.select(cfg.get("folder", "INBOX"), readonly=True)
        typ, data = imap.search(None, f'(SINCE "{since}")')
        if typ != "OK":
            raise RuntimeError(f"IMAP search failed: {typ}")
        ids = data[0].split()[-int(cfg.get("max_messages", 300)):]
        for num in ids:
            typ, parts = imap.fetch(num, "(BODY.PEEK[])")
            if typ != "OK" or not parts or not isinstance(parts[0], tuple):
                continue
            msg = email.message_from_bytes(parts[0][1])
            message_id = (msg.get("Message-ID") or f"imap-{num.decode()}").strip()
            subject = _decode(msg.get("Subject"))
            sender = _decode(msg.get("From"))
            try:
                received = parsedate_to_datetime(msg.get("Date", "")).isoformat()
            except Exception:
                received = ""
            stats["scanned"] += 1
            new_status = process_message(conn, message_id, subject, sender, _body(msg), received)
            if new_status:
                stats["updated"] += 1
                stats[new_status] = stats.get(new_status, 0) + 1
                log.info("Email '%s' -> %s", subject[:80], new_status)
    finally:
        try:
            imap.logout()
        except Exception:
            pass
        conn.close()
    return stats
