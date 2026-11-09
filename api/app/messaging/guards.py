"""Send guards. Medical-content guard: no treatment, condition or specialty detail may ever leave the system."""
from __future__ import annotations

import logging
import unicodedata
from datetime import datetime, time, timedelta

from ..timeutil import IST, UTC, ist_to_utc, to_ist

log = logging.getLogger("showup.guards")

# Needs review by native speakers before go-live (see DECISIONS.md).
BLOCKLIST: dict[str, list[str]] = {
    "en": [
        "root canal", "extraction", "extractions", "braces", "filling", "fillings", "implant", "implants",
        "scaling", "pain", "painful", "infection", "infections", "diagnosis", "surgery", "surgical",
        "toothache", "cavity", "cavities", "decay", "gum disease", "treatment", "procedure", "medication",
        "prescription", "antibiotic", "antibiotics", "x-ray", "xray", "anaesthesia", "anesthesia", "sedation",
        "orthodontic", "endodontic", "denture", "dentures",
    ],
    "hi": [
        "रूट कैनाल", "दांत निकालना", "दाँत निकालना", "ब्रेसेस", "फिलिंग", "इम्प्लांट", "इम्प्लान्ट", "स्केलिंग",
        "दर्द", "संक्रमण", "निदान", "सर्जरी", "इलाज", "उपचार", "दवा", "दंत रोग",
    ],
    "mr": [
        "रूट कॅनॉल", "दात काढणे", "ब्रेसेस", "फिलिंग", "इम्प्लांट", "स्केलिंग", "वेदना", "दुखणे", "संसर्ग",
        "निदान", "शस्त्रक्रिया", "उपचार", "औषध", "दंत रोग",
    ],
}
_ALL_TERMS = sorted({t.casefold() for ts in BLOCKLIST.values() for t in ts})


def _is_word_char(ch: str) -> bool:
    # Letters, combining marks (Devanagari matras) and digits count as part of a word.
    return unicodedata.category(ch)[0] in "LMN"


def _contains(text: str, term: str) -> bool:
    if not term.isascii():  # Hindi/Marathi inflect heavily (दर्द, दर्दनाक): any occurrence counts
        return term in text
    start = 0
    while (i := text.find(term, start)) != -1:
        j = i + len(term)
        if (i == 0 or not _is_word_char(text[i - 1])) and (j == len(text) or not _is_word_char(text[j])):
            return True
        start = i + 1
    return False


def find_medical_terms(text: str, treatment_names: list[str] | tuple[str, ...] = ()) -> list[str]:
    """Whole-word / whole-phrase matches of (a) every clinic treatment name and (b) the en/hi/mr blocklist."""
    norm = " ".join(unicodedata.normalize("NFC", text).casefold().split())
    terms = set(_ALL_TERMS) | {" ".join(n.casefold().split()) for n in treatment_names if n and n.strip()}
    return sorted(t for t in terms if _contains(norm, t))


def is_quiet(local_t: time, start: time, end: time) -> bool:
    """Quiet window may wrap midnight (21:00 -> 08:00)."""
    if start <= end:
        return start <= local_t < end
    return local_t >= start or local_t < end


def shift_for_quiet_hours(due: datetime, now: datetime, start: time, end: time) -> datetime:
    """Proactive sends inside quiet hours move to the nearest earlier allowed time (the moment quiet hours began),
    or to the next quiet-hours end (08:00) if that moment has already passed."""
    local = to_ist(due)
    if not is_quiet(local.time(), start, end):
        return due
    if local.time() >= start:                       # evening: quiet began earlier today
        earlier = ist_to_utc(local.date(), start)
        morning = ist_to_utc(local.date() + timedelta(days=1), end)
    else:                                            # small hours: quiet began yesterday evening
        earlier = ist_to_utc(local.date() - timedelta(days=1), start)
        morning = ist_to_utc(local.date(), end)
    return earlier if earlier >= now else morning


def choose_transport(clinic_mode: str, *, is_demo_seed: bool) -> str:
    """Seeded demo people can NEVER get a real WhatsApp message, whatever the clinic mode says."""
    return "simulator" if clinic_mode != "live" or is_demo_seed else "live"
