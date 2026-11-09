"""Hindi and Marathi templates: complete, structurally identical to English, safe, and Meta-submittable."""
import re

import pytest

from app.messaging import guards, templates

PATIENT = [n for n, t in templates.load_all().items() if n != "showup_daily_summary"]
VAR = re.compile(r"\{\{(\d+)\}\}")


@pytest.mark.parametrize("lang", ["hi", "mr"])
@pytest.mark.parametrize("name", PATIENT)
def test_translation_matches_english_structure(name, lang):
    t = templates.get_template(name)
    en, tr = t["languages"]["en"], t["languages"][lang]
    assert VAR.findall(tr["body"]) == VAR.findall(en["body"])            # same variables, same order (Meta needs them sequential)
    assert set(tr["button_labels"]) == set(en["button_labels"])
    assert all(0 < len(v) <= 25 for v in tr["button_labels"].values())   # Meta quick-reply limit
    assert not tr["body"].startswith("{{") and not tr["body"].rstrip().endswith("}}")   # Meta rejects variables at the edges
    assert bool(tr["footer"]) == bool(en["footer"])
    if tr["footer"]:
        assert "STOP" in tr["footer"]                                     # the opt-out keyword stays in Latin letters
    assert len(tr["example"]) == len(t["variables"])
    assert re.search(r"[\u0900-\u097F]", tr["body"])


@pytest.mark.parametrize("lang", ["hi", "mr"])
@pytest.mark.parametrize("name", PATIENT)
def test_rendered_translation_passes_the_medical_content_guard(name, lang):
    r = templates.render(name, lang, templates.sample_values(name, lang), ids={"appointment_id": "a", "offer_id": "o", "patient_id": "p"})
    assert r.language == lang
    assert guards.find_medical_terms(" ".join([r.body, r.footer or "", *[b["text"] for b in r.buttons]]), []) == []
    meta = templates.to_meta_template(name, lang)
    assert meta["language"] == lang and meta["components"][0]["type"] == "BODY"


def test_unknown_language_still_falls_back_to_english():
    r = templates.render("showup_day_before", "ta", templates.sample_values("showup_day_before"), ids={"appointment_id": "a"})
    assert r.language == "en"
