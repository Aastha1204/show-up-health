"""CSV / published-Google-Sheet import: parse -> map columns -> validate every row -> commit the valid ones.

Nothing is trusted from the preview: `commit` re-validates from the raw CSV text. Re-importing the same file is safe
(rows that already exist are reported, not duplicated). Imported people must have agreed to WhatsApp updates; the caller
has to confirm that explicitly."""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from urllib.parse import urlparse

import httpx

from ..phone import normalize_phone
from ..timeutil import IST, ist_to_utc, utcnow
from . import appointments as svc
from .slots import fetch_busy, is_slot_valid, overlaps

MAX_ROWS = 1000
MAX_BYTES = 2 * 1024 * 1024
FIELDS = ["patient_name", "phone", "doctor", "date", "time", "treatment", "language", "note"]
REQUIRED = ["patient_name", "phone", "doctor", "date", "time"]
LABELS = {"patient_name": "Patient name", "phone": "Phone", "doctor": "Doctor", "date": "Date", "time": "Time",
          "treatment": "Treatment (optional)", "language": "Language (optional)", "note": "Note (optional)"}
GUESSES = {
    "patient_name": ["patient name", "patient", "name", "full name"],
    "phone": ["phone", "mobile", "whatsapp", "contact", "phone number", "mobile number"],
    "doctor": ["doctor", "dr", "provider", "dentist"],
    "date": ["date", "appointment date", "day"],
    "time": ["time", "appointment time", "slot", "start", "start time"],
    "treatment": ["treatment", "service", "procedure", "type", "visit type"],
    "language": ["language", "lang"],
    "note": ["note", "notes", "remarks", "comment", "comments"],
}


class ImportError_(Exception):
    def __init__(self, message: str, code: str = "bad_input", status: int = 422):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


# ───────────────────────── parsing ─────────────────────────
def parse_csv(text: str) -> tuple[list[str], list[dict[str, str]]]:
    if len(text.encode("utf-8", "ignore")) > MAX_BYTES:
        raise ImportError_("That file is larger than 2 MB.", "too_large", 413)
    text = text.lstrip("﻿")
    if not text.strip():
        raise ImportError_("The file is empty.", "empty")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows = [r for r in reader if any(c.strip() for c in r)]
    if len(rows) < 2:
        raise ImportError_("Add a header row and at least one appointment row.", "no_rows")
    headers = [h.strip() for h in rows[0]]
    if len(set(h.lower() for h in headers)) != len(headers) or "" in headers:
        raise ImportError_("Column headers must be unique and not blank.", "bad_headers")
    body = rows[1:]
    if len(body) > MAX_ROWS:
        raise ImportError_(f"A file can hold at most {MAX_ROWS} rows (this one has {len(body)}). Split it up.", "too_many_rows", 413)
    return headers, [{h: (r[i].strip() if i < len(r) else "") for i, h in enumerate(headers)} for r in body]


def guess_mapping(headers: list[str]) -> dict[str, str | None]:
    norm = {re.sub(r"[^a-z0-9]+", " ", h.lower()).strip(): h for h in headers}
    out: dict[str, str | None] = {}
    used: set[str] = set()
    for f in FIELDS:
        pick = next((norm[g] for g in GUESSES[f] if g in norm and norm[g] not in used), None)
        out[f] = pick
        if pick:
            used.add(pick)
    return out


_DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y"]


def parse_date(s: str) -> date | None:
    s = s.strip().replace(",", "")
    for f in _DATE_FORMATS:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            pass
    return None


def parse_time(s: str) -> time | None:
    s = s.strip().lower().replace(".", ":")
    for f in ("%H:%M", "%I:%M %p", "%I:%M%p", "%I %p", "%I%p", "%H%M"):
        try:
            return datetime.strptime(s, f).time()
        except ValueError:
            pass
    return None


def _norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).casefold()
    s = re.sub(r"\b(dr|doctor)\b\.?", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def match_doctor(value: str, doctors: list[dict]) -> tuple[dict | None, str | None]:
    n = _norm_name(value)
    if not n:
        return None, "Doctor is blank"
    exact = [d for d in doctors if _norm_name(d["name"]) == n]
    loose = exact or [d for d in doctors if n in _norm_name(d["name"]) or _norm_name(d["name"]) in n]
    if len(loose) == 1:
        return loose[0], None
    if not loose:
        return None, f"No doctor matches “{value}”"
    return None, f"“{value}” matches more than one doctor"


def match_treatment(value: str, tts: list[dict]) -> tuple[dict | None, str | None]:
    if not value.strip():
        default = next((t for t in tts if t["name"].casefold() == "consultation"), None) or min(tts, key=lambda t: t["duration_min"])
        return default, None
    n = _norm_name(value)
    hit = [t for t in tts if _norm_name(t["name"]) == n] or [t for t in tts if n and (n in _norm_name(t["name"]))]
    return (hit[0], None) if len(hit) == 1 else (None, f"No treatment matches “{value}”" if not hit else f"“{value}” matches more than one treatment")


LANG = {"en": "en", "english": "en", "hi": "hi", "hindi": "hi", "mr": "mr", "marathi": "mr"}


# ───────────────────────── validation ─────────────────────────
@dataclass
class RowResult:
    row: int                                   # 1-based data row (header excluded), as the person sees it in the sheet
    raw: dict[str, str]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    data: dict | None = None

    @property
    def ok(self) -> bool:
        return not self.errors

    def public(self) -> dict:
        d = self.data or {}
        return {"row": self.row, "ok": self.ok, "errors": self.errors, "warnings": self.warnings, "raw": self.raw,
                "summary": ({"patient": d["name"], "phone": d["phone"], "doctor": d["doctor_name"], "treatment": d["treatment_name"],
                             "start_at": d["start_at"].isoformat(), "language": d["language"]} if d else None)}


async def validate_rows(conn, clinic_id, rows: list[dict[str, str]], mapping: dict[str, str | None], now: datetime | None = None) -> list[RowResult]:
    now = now or utcnow()
    missing = [LABELS[f] for f in REQUIRED if not mapping.get(f)]
    if missing:
        raise ImportError_("Choose a column for: " + ", ".join(missing), "mapping_incomplete")
    doctors = await (await conn.execute("select * from doctor where clinic_id=%s and active", [clinic_id])).fetchall()
    tts = await (await conn.execute("select * from treatment_type where clinic_id=%s", [clinic_id])).fetchall()
    busy_cache: dict[tuple, list] = {}          # (doctor, date) -> DB busy + rows accepted so far in THIS file
    owner_of: dict[tuple, int] = {}             # interval -> row number that claimed it
    seen_patient_slot: dict[tuple, int] = {}
    out: list[RowResult] = []

    for i, raw in enumerate(rows, start=1):
        r = RowResult(i, raw)
        cell = lambda f: raw.get(mapping[f] or "", "").strip() if mapping.get(f) else ""  # noqa: E731
        name = " ".join(cell("patient_name").split())
        if not name:
            r.errors.append("Patient name is blank")
        phone = None
        try:
            phone = normalize_phone(cell("phone"))
        except ValueError as e:
            r.errors.append(f"Phone: {e}")
        doctor, derr = match_doctor(cell("doctor"), doctors)
        if derr:
            r.errors.append(derr)
        tt, terr = match_treatment(cell("treatment"), tts)
        if terr:
            r.errors.append(terr)
        d, t = parse_date(cell("date")), parse_time(cell("time"))
        if not d:
            r.errors.append(f"Date “{cell('date')}” is not a date I understand (try 2026-11-10 or 10/11/2026)")
        if not t:
            r.errors.append(f"Time “{cell('time')}” is not a time I understand (try 10:30 or 10:30 AM)")
        lang_raw = cell("language").casefold()
        lang = LANG.get(lang_raw, "en")
        if lang_raw and lang_raw not in LANG:
            r.warnings.append(f"Language “{cell('language')}” not recognised: using English")

        if doctor and tt and d and t:
            start = ist_to_utc(d, t)
            if start <= now:
                r.errors.append("This appointment is in the past")
            elif not is_slot_valid(doctor["working_hours"], start, tt["duration_min"]):
                r.errors.append("Outside the doctor's working hours, or not on the 15-minute grid")
            else:
                end = start + timedelta(minutes=tt["duration_min"])
                key = (doctor["id"], d)
                if key not in busy_cache:
                    busy_cache[key] = [(b0, b1, None) for b0, b1 in await fetch_busy(conn, doctor["id"], d)]
                clash = next((b for b in busy_cache[key] if overlaps(start, end, b[0], b[1])), None)
                if clash:
                    r.errors.append(f"Overlaps row {clash[2]} of this file" if clash[2] else "Overlaps an existing appointment for this doctor")
                if phone:
                    dup = await (await conn.execute(
                        """select 1 as x from appointment a join patient p on p.id=a.patient_id where a.clinic_id=%s and p.phone=%s
                           and a.start_at=%s and a.status in ('pending','confirmed','rescheduled','arrived')""", [clinic_id, phone, start])).fetchone()
                    if dup:
                        r.errors = [e for e in r.errors if not e.startswith("Overlaps an existing")] + ["Already booked (this patient at this time)"]
                    elif (phone, start) in seen_patient_slot:
                        r.errors.append(f"Same patient and time as row {seen_patient_slot[(phone, start)]}")
                if not r.errors:
                    busy_cache[key].append((start, end, i))
                    if phone:
                        seen_patient_slot[(phone, start)] = i
                if name and phone and not r.errors:
                    r.data = {"name": name, "phone": phone, "doctor_id": doctor["id"], "doctor_name": doctor["name"],
                              "treatment_id": tt["id"], "treatment_name": tt["name"], "start_at": start, "language": lang,
                              "note": cell("note") or None}
        out.append(r)
    return out


def summarise(results: list[RowResult]) -> dict:
    return {"total": len(results), "ok": sum(r.ok for r in results), "errors": sum(not r.ok for r in results),
            "warnings": sum(bool(r.warnings) for r in results)}


async def commit(conn, clinic_id, results: list[RowResult], *, send_confirmations: bool, now: datetime | None = None) -> dict:
    """Create every valid row; each in its own savepoint so one failure cannot undo the rest."""
    now = now or utcnow()
    created, skipped = 0, []
    for r in results:
        if not r.ok or not r.data:
            skipped.append({"row": r.row, "reason": "; ".join(r.errors) or "invalid"})
            continue
        d = r.data
        try:
            async with conn.transaction():
                p = await svc.get_or_create_patient(conn, clinic_id, {
                    "name": d["name"], "phone": d["phone"], "language": d["language"], "consent_source": "import"})
                await svc.create_appointment(
                    conn, clinic_id=clinic_id, patient_id=p["id"], doctor_id=d["doctor_id"], treatment_type_id=d["treatment_id"],
                    start_at=d["start_at"], source="import", internal_note=d["note"], now=now, send_confirmation=send_confirmations)
            created += 1
        except svc.DomainError as e:           # e.g. a booking made since the preview took the slot
            skipped.append({"row": r.row, "reason": e.message})
    return {"created": created, "skipped": skipped}


# ───────────────────────── published Google Sheet ─────────────────────────
SHEET_HOSTS = {"docs.google.com"}
REDIRECT_SUFFIX = ".googleusercontent.com"


def normalise_sheet_url(url: str) -> str:
    """Accept only https Google Sheets links. A normal share link (/d/<id>/edit) becomes its CSV export link."""
    u = urlparse(url.strip())
    if u.scheme != "https" or u.hostname not in SHEET_HOSTS or u.port not in (None, 443) or u.username or u.password:
        raise ImportError_("Paste a link from Google Sheets (https://docs.google.com/spreadsheets/...).", "bad_sheet_url")
    m = re.match(r"^/spreadsheets/d/(?!e/)([A-Za-z0-9_-]+)", u.path)
    if m:
        gid = re.search(r"gid=(\d+)", u.query + "&" + u.fragment)
        return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv" + (f"&gid={gid.group(1)}" if gid else "")
    if not u.path.startswith("/spreadsheets/"):
        raise ImportError_("That link is not a Google Sheet.", "bad_sheet_url")
    return url.strip()


def _host_ok(host: str | None) -> bool:
    return bool(host) and (host in SHEET_HOSTS or host.endswith(REDIRECT_SUFFIX))


async def fetch_sheet_csv(url: str, client: httpx.AsyncClient | None = None) -> str:
    """SSRF-safe fetch: only Google hosts, https only, at most 3 manual redirects (each re-checked), 10 s, 2 MB."""
    url = normalise_sheet_url(url)
    own = client is None
    client = client or httpx.AsyncClient(timeout=10, follow_redirects=False)
    try:
        for _ in range(4):
            u = urlparse(url)
            if u.scheme != "https" or not _host_ok(u.hostname):
                raise ImportError_("The link redirected somewhere that is not Google Sheets.", "bad_sheet_url")
            async with client.stream("GET", url, headers={"Accept": "text/csv"}) as resp:
                if resp.is_redirect:
                    url = str(resp.headers.get("location", ""))
                    if url.startswith("/"):
                        url = f"https://{u.hostname}{url}"
                    continue
                if resp.status_code != 200:
                    raise ImportError_("Google would not give us that sheet. Publish it to the web as CSV (File > Share > Publish to web > CSV), "
                                       "or share it so anyone with the link can view it.", "sheet_unavailable", 422)
                ctype = resp.headers.get("content-type", "")
                if "csv" not in ctype and "text/plain" not in ctype:
                    raise ImportError_("That link did not return a CSV. In Google Sheets use File > Share > Publish to web > CSV.", "not_csv")
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf += chunk
                    if len(buf) > MAX_BYTES:
                        raise ImportError_("That sheet is larger than 2 MB.", "too_large", 413)
                return buf.decode("utf-8-sig", errors="replace")
        raise ImportError_("Too many redirects.", "bad_sheet_url")
    except httpx.HTTPError:
        raise ImportError_("Could not reach Google Sheets. Try again, or upload the CSV file instead.", "sheet_unreachable", 502)
    finally:
        if own:
            await client.aclose()
