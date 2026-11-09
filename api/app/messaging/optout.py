"""Is this free-text message a request to stop messaging? Deliberately conservative in both directions:
missing a STOP is a compliance problem, but opting someone out because they wrote "can you stop by at 5?" is a
service problem, so only SHORT messages count, and negations ("don't stop") never do."""
from __future__ import annotations

import difflib
import re
import unicodedata

STOP_WORDS = {
    "stop", "stopall", "unsubscribe", "unsub", "optout", "cancelsubscription",
    # Hindi / Marathi (needs native-speaker review before go-live)
    "बंद", "बन्द", "रोको", "रोकें", "रोकिए", "थांबा", "थांबवा",
}
FUZZY_TARGETS = ["stop", "unsubscribe"]
NEGATIONS = {"dont", "don", "not", "never", "no", "मत", "नहीं", "नको", "नका"}
MAX_WORDS = 3


def _words(text: str) -> list[str]:
    t = unicodedata.normalize("NFC", text).casefold()
    t = re.sub(r"[’']", "", t)                       # don't -> dont
    t = "".join(ch if unicodedata.category(ch)[0] in "LMN" else " " for ch in t)
    return t.split()


def is_stop_request(text: str) -> bool:
    words = _words(text or "")
    if not words or len(words) > MAX_WORDS or any(w in NEGATIONS for w in words):
        return False
    for w in words:
        if w in STOP_WORDS:
            return True
        if len(w) >= 4 and w.isascii() and difflib.get_close_matches(w, FUZZY_TARGETS, n=1, cutoff=0.8):
            return True                                    # stopp, stoop, unsubcribe ...
    return False
