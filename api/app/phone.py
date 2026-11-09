import re

_E164 = re.compile(r"^\+[1-9][0-9]{7,14}$")
_IN_MOBILE = re.compile(r"^\+91[6-9][0-9]{9}$")


def normalize_phone(raw: str) -> str:
    """Return E.164. Bare 10-digit Indian mobiles get +91. Raises ValueError otherwise."""
    s = re.sub(r"[\s\-().]", "", raw or "")
    if re.fullmatch(r"[6-9][0-9]{9}", s):
        s = "+91" + s
    elif re.fullmatch(r"0[6-9][0-9]{9}", s):
        s = "+91" + s[1:]
    elif re.fullmatch(r"91[6-9][0-9]{9}", s):
        s = "+" + s
    elif s.startswith("00"):
        s = "+" + s[2:]
    if not _E164.match(s):
        raise ValueError("Enter a valid phone number, e.g. +91 98765 43210")
    if s.startswith("+91") and not _IN_MOBILE.match(s):
        raise ValueError("Indian mobile numbers are 10 digits starting 6-9")
    return s


def normalize_any_phone(raw: str) -> str:
    """For a clinic's own number, which is often a LANDLINE: any valid E.164 number, or an Indian number with a leading 0
    (STD code) such as 0251 2345678. Mobile-only rules (`normalize_phone`) still apply to patients and doctors."""
    try:
        return normalize_phone(raw)
    except ValueError:
        pass
    s = re.sub(r"[\s\-().]", "", raw or "")
    if re.fullmatch(r"0[1-9]\d{8,10}", s):
        s = "+91" + s[1:]
    if not _E164.match(s):
        raise ValueError("Enter a full phone number, e.g. +91 25 1234 5678")
    return s
