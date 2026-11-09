"""iCalendar (RFC 5545) feed of one doctor's appointments, for "subscribe by URL" in Google Calendar, Apple Calendar or Outlook.

Deliberately minimal and privacy-first: the event title is the patient's name only. No phone number, no treatment, no notes.
Cancelled and no-show appointments are left out, so a cancelled booking disappears from the calendar at its next refresh.
Google Calendar refreshes subscribed URLs on its own schedule (often several hours): this is a convenience feed, not live sync."""
from __future__ import annotations

from datetime import datetime, timezone

STATUS_ICS = {"pending": "TENTATIVE", "confirmed": "CONFIRMED", "rescheduled": "CONFIRMED", "arrived": "CONFIRMED"}
STATUS_TEXT = {"pending": "Not yet confirmed", "confirmed": "Confirmed", "rescheduled": "Rescheduled (confirmed)", "arrived": "Arrived"}


def esc(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n"))


def fold(line: str) -> str:
    """Lines are limited to 75 OCTETS (not characters); continuation lines start with one space."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > (75 if not out else 74):
            out.append(cur)
            cur, size = "", 0
        cur += ch
        size += n
    out.append(cur)
    return "\r\n ".join(out)


def utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build(doctor_name: str, clinic_name: str, rows: list[dict], now: datetime) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Neovia AI//ShowUp//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             f"X-WR-CALNAME:{esc(doctor_name + ' · ' + clinic_name)}", "X-WR-TIMEZONE:Asia/Kolkata",
             "REFRESH-INTERVAL;VALUE=DURATION:PT1H", "X-PUBLISHED-TTL:PT1H"]
    for r in rows:
        if r["status"] not in STATUS_ICS:
            continue
        lines += ["BEGIN:VEVENT", f"UID:{r['id']}@showup", f"DTSTAMP:{utc(now)}", f"LAST-MODIFIED:{utc(r['updated_at'])}",
                  f"DTSTART:{utc(r['start_at'])}", f"DTEND:{utc(r['end_at'])}", f"SUMMARY:{esc(r['patient_name'])}",
                  f"DESCRIPTION:{esc('Status: ' + STATUS_TEXT[r['status']])}", f"STATUS:{STATUS_ICS[r['status']]}",
                  f"LOCATION:{esc(clinic_name)}", "TRANSP:OPAQUE", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"
