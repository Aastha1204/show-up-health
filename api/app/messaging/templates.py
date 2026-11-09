"""Loads /templates/*.json and renders them. The same renderer feeds the Simulator and the Meta submission payload,
so what the presenter sees is character-for-character what WhatsApp would show."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parents[3] / "templates"
_PLACEHOLDER = re.compile(r"\{\{(\d+)\}\}")


class TemplateError(ValueError):
    pass


@dataclass
class Rendered:
    template_name: str
    language: str
    body: str
    footer: str | None
    buttons: list[dict]  # [{"id","text","payload"}]
    params: list[str]    # ordered values for {{1}}, {{2}}... (what the Cloud API is sent)


@lru_cache
def load_all() -> dict[str, dict]:
    out = {}
    for f in sorted(TEMPLATE_DIR.glob("*.json")):
        t = json.loads(f.read_text(encoding="utf-8"))
        out[t["name"]] = t
    return out


def get_template(name: str) -> dict:
    try:
        return load_all()[name]
    except KeyError:
        raise TemplateError(f"unknown template {name!r}")


def one_line(value) -> str:
    """Template variable values cannot contain newlines/tabs (Meta rejects them); collapse all whitespace."""
    return re.sub(r"\s+", " ", str(value)).strip()


def render(name: str, language: str, values: dict, ids: dict | None = None) -> Rendered:
    """`values` is keyed by variable name; `ids` fills button payload placeholders (appointment_id, offer_id, ...)."""
    t = get_template(name)
    lang = language if language in t["languages"] else "en"
    spec = t["languages"][lang]
    missing = [v for v in t["variables"] if v not in values]
    if missing:
        raise TemplateError(f"{name}: missing values {missing}")
    for k, v in values.items():
        if re.search(r"[\r\n\t]", str(v)):
            raise TemplateError(f"{name}: value for {k!r} contains a newline/tab")
    body = _PLACEHOLDER.sub(lambda m: str(values[t["variables"][int(m.group(1)) - 1]]), spec["body"])
    buttons = []
    for b in t["buttons"]:
        payload = b["payload"].format(**(ids or {})) if ids is not None else b["payload"]
        buttons.append({"id": b["id"], "text": spec["button_labels"][b["id"]], "payload": payload})
    params = [str(values[v]) for v in t["variables"]]
    return Rendered(name, lang, body, spec.get("footer"), buttons, params)


def sample_values(name: str, language: str = "en") -> dict:
    t = get_template(name)
    lang = language if language in t["languages"] else "en"
    return dict(zip(t["variables"], t["languages"][lang]["example"]))


def to_meta_template(name: str, language: str) -> dict:
    """Body for POST /{waba_id}/message_templates (or to copy into WhatsApp Manager)."""
    t = get_template(name)
    spec = t["languages"][language]
    comps: list[dict] = [{"type": "BODY", "text": spec["body"], "example": {"body_text": [spec["example"]]}}]
    if spec.get("footer"):
        comps.append({"type": "FOOTER", "text": spec["footer"]})
    if t["buttons"]:
        comps.append({"type": "BUTTONS", "buttons": [
            {"type": "QUICK_REPLY", "text": spec["button_labels"][b["id"]]} for b in t["buttons"]]})
    return {"name": name, "category": t["category"], "language": {"en": "en", "hi": "hi", "mr": "mr"}[language],
            "components": comps}
