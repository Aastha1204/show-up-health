"""Pure messaging tests: templates, medical-content guard, quiet hours, transport choice, webhook parsing/signature."""
import json
import re
from datetime import datetime, time, timedelta, timezone

import pytest

from app.messaging import guards, meta_payloads as mp, templates, webhook
from app.seed import TREATMENTS
from app.timeutil import IST, ist_to_utc

TREATMENT_NAMES = [t[0] for t in TREATMENTS]
ALL = templates.load_all()
LANG_CASES = [(n, lang) for n, t in ALL.items() for lang in t["languages"]]
D = datetime(2030, 1, 7).date()  # a Monday


def ist(d, hh, mm=0):
    return ist_to_utc(d, time(hh, mm))


# ───────── templates ─────────
def test_all_nine_templates_exist():
    assert set(ALL) == {"showup_booking_confirmation", "showup_day_before", "showup_two_hour", "showup_post_visit",
                        "showup_recall", "showup_waitlist_offer", "showup_running_late", "showup_daily_summary", "showup_deposit_request"}


@pytest.mark.parametrize("name,lang", LANG_CASES)
def test_template_is_meta_submittable(name, lang):
    t, spec = ALL[name], ALL[name]["languages"][lang]
    body = spec["body"]
    assert t["category"] == "UTILITY"
    assert not body.startswith("{{") and not body.rstrip().endswith("}}"), "body must start and end with text"
    nums = [int(n) for n in re.findall(r"\{\{(\d+)\}\}", body)]
    assert nums == list(range(1, len(nums) + 1)), "placeholders must be sequential {{1}}..{{n}}"
    assert len(nums) == len(t["variables"]) == len(spec["example"])
    assert len(body) <= 1024 and len(spec.get("footer") or "") <= 60
    assert len(t["buttons"]) <= 3 and set(spec["button_labels"]) == {b["id"] for b in t["buttons"]}
    assert all(len(v) <= 25 for v in spec["button_labels"].values())
    assert not any(re.search(r"[\r\n\t]", e) for e in spec["example"])
    meta = templates.to_meta_template(name, lang)
    assert meta["components"][0]["example"]["body_text"] == [spec["example"]]


def test_day_before_renders_exactly_and_sets_button_payloads():
    r = templates.render("showup_day_before", "en", templates.sample_values("showup_day_before"),
                         ids={"appointment_id": "A1", "patient_id": "P1"})
    assert r.body == ("Hi Priya, this is a reminder of your appointment at SmileCare Dental Clinic tomorrow, "
                      "Tue 10 Nov at 10:30 AM with Dr. Neha Kulkarni. Please confirm, or choose a new time if "
                      "this no longer suits you.")
    assert [(b["text"], b["payload"]) for b in r.buttons] == [
        ("Confirm", "CONFIRM:A1"), ("Reschedule", "RESCHEDULE:A1"), ("Cancel", "CANCEL:A1")]


def test_waitlist_and_recall_button_payloads():
    w = templates.render("showup_waitlist_offer", "en", templates.sample_values("showup_waitlist_offer"), {"offer_id": "O9"})
    assert [b["payload"] for b in w.buttons] == ["WL_BOOK:O9", "WL_SKIP:O9"]
    r = templates.render("showup_recall", "en", templates.sample_values("showup_recall"), {"patient_id": "P7"})
    assert [b["payload"] for b in r.buttons] == ["RECALL_BOOK:P7", "RECALL_SKIP:P7"]


def test_booking_confirmation_has_stop_footer():
    r = templates.render("showup_booking_confirmation", "en", templates.sample_values("showup_booking_confirmation"))
    assert r.footer == "Reply STOP to stop these updates" and r.buttons == []


def test_render_rejects_newlines_and_missing_values():
    v = templates.sample_values("showup_two_hour")
    with pytest.raises(templates.TemplateError):
        templates.render("showup_two_hour", "en", {**v, "parking": "line one\nline two"})
    with pytest.raises(templates.TemplateError):
        templates.render("showup_two_hour", "en", {k: x for k, x in v.items() if k != "parking"})
    assert templates.one_line("a \n  b\t c ") == "a b c"


def test_unknown_language_falls_back_to_english():
    assert templates.render("showup_running_late", "ta", templates.sample_values("showup_running_late")).language == "en"


# ───────── medical-content guard ─────────
@pytest.mark.parametrize("name,lang", LANG_CASES)
def test_every_template_in_every_language_passes_the_guard(name, lang):
    r = templates.render(name, lang, templates.sample_values(name, lang),
                         ids={"appointment_id": "A", "patient_id": "P", "offer_id": "O"})
    text = " ".join(filter(None, [r.body, r.footer, *[b["text"] for b in r.buttons]]))
    assert guards.find_medical_terms(text, TREATMENT_NAMES) == []


@pytest.mark.parametrize("treatment", TREATMENT_NAMES)
def test_every_treatment_name_blocks_a_body(treatment):
    assert guards.find_medical_terms(f"Hi Priya, your {treatment} is booked for Tue 10 Nov.", TREATMENT_NAMES)
    assert guards.find_medical_terms(f"HI PRIYA, YOUR {treatment.upper()} IS BOOKED", TREATMENT_NAMES)


@pytest.mark.parametrize("lang", ["en", "hi", "mr"])
def test_every_blocklist_term_blocks(lang):
    for term in guards.BLOCKLIST[lang]:
        assert guards.find_medical_terms(f"Hi Priya, regarding your {term} appointment.", []), term


def test_hindi_and_marathi_inflections_are_caught():
    assert guards.find_medical_terms("आपका दर्दनाक अनुभव", [])
    assert guards.find_medical_terms("तुमचे दात काढणे उद्या आहे", [])


@pytest.mark.parametrize("ok", ["Spain is lovely", "He is painting a wall", "Free parking inside the society compound",
                                "Hi Priya, your appointment is tomorrow", "It was a refilling station"])
def test_no_false_positives_on_ordinary_words(ok):
    assert guards.find_medical_terms(ok, TREATMENT_NAMES) == []


# ───────── quiet hours ─────────
QS, QE = time(21, 0), time(8, 0)


@pytest.mark.parametrize("t,quiet", [("20:59", False), ("21:00", True), ("23:59", True), ("00:00", True),
                                     ("07:59", True), ("08:00", False), ("12:00", False)])
def test_is_quiet(t, quiet):
    h, m = t.split(":")
    assert guards.is_quiet(time(int(h), int(m)), QS, QE) is quiet


def test_quiet_shift_cases():
    shift = lambda due, now: guards.shift_for_quiet_hours(due, now, QS, QE)  # noqa: E731
    assert shift(ist(D, 10), ist(D, 9)) == ist(D, 10)                                   # allowed: untouched
    assert shift(ist(D, 22), ist(D, 15)) == ist(D, 21)                                  # evening -> nearest earlier allowed
    assert shift(ist(D, 2), ist(D, 1) - timedelta(days=1)) == ist(D - timedelta(days=1), 21)  # small hours -> 21:00 before
    assert shift(ist(D, 22), ist(D, 23)) == ist(D + timedelta(days=1), 8)               # 21:00 already past -> 08:00 next
    assert shift(ist(D, 3), ist(D, 2)) == ist(D, 8)                                     # none earlier -> 08:00


def test_transport_choice_never_sends_seeds_live():
    c = guards.choose_transport
    assert c("simulator", is_demo_seed=False) == "simulator"
    assert c("simulator", is_demo_seed=True) == "simulator"
    assert c("live", is_demo_seed=True) == "simulator"
    assert c("live", is_demo_seed=False) == "live"


# ───────── webhook parsing & signature ─────────
PHONE = "+919876500000"


def test_parse_template_button_tap():
    (ev,) = webhook.parse_webhook(mp.button_payload(PHONE, "CONFIRM:abc", "Confirm", context_wamid="wamid.X", msg_id="m1"))
    assert (ev.kind, ev.payload, ev.text, ev.context_wamid, ev.from_phone, ev.dedupe_key) == (
        "button", "CONFIRM:abc", "Confirm", "wamid.X", PHONE, "msg:m1")


def test_parse_list_reply_button_reply_and_text():
    (l,) = webhook.parse_webhook(mp.list_reply_payload(PHONE, "RS:a:d:1", "Tue 10 Nov · 10:30 AM", "Dr. Neha", msg_id="m2"))
    assert (l.kind, l.payload, l.text, l.event_name) == ("list_reply", "RS:a:d:1", "Tue 10 Nov · 10:30 AM", "list_reply")
    (b,) = webhook.parse_webhook(mp.button_reply_payload(PHONE, "WL_BOOK:o", "Book", msg_id="m3"))
    assert (b.kind, b.payload, b.event_name) == ("button_reply", "WL_BOOK:o", "button")
    (t,) = webhook.parse_webhook(mp.text_payload(PHONE, "STOP", msg_id="m4"))
    assert (t.kind, t.text, t.event_name) == ("text", "STOP", "text")


@pytest.mark.parametrize("status", ["sent", "delivered", "read"])
def test_parse_statuses(status):
    (ev,) = webhook.parse_webhook(mp.status_payload("wamid.Z", status, PHONE))
    assert (ev.wamid, ev.status, ev.dedupe_key) == ("wamid.Z", status, f"status:wamid.Z:{status}")


def test_parse_failed_status_keeps_meta_error():
    (ev,) = webhook.parse_webhook(mp.status_payload("wamid.Z", "failed", PHONE,
                                                    [{"code": 131047, "title": "Re-engagement message"}]))
    assert ev.status == "failed" and ev.errors[0]["code"] == 131047


def test_unsupported_and_empty_payloads_are_ignored():
    p = mp.text_payload(PHONE, "x", msg_id="m5")
    p["entry"][0]["changes"][0]["value"]["messages"][0]["type"] = "image"
    assert webhook.parse_webhook(p) == [] and webhook.parse_webhook({}) == []


def test_signature_verification():
    body = json.dumps({"a": 1}).encode()
    good = webhook.sign(body, "s3cret")
    assert webhook.verify_signature(body, good, "s3cret")
    assert not webhook.verify_signature(body + b" ", good, "s3cret")        # body tampered
    assert not webhook.verify_signature(body, good, "other")
    assert not webhook.verify_signature(body, None, "s3cret")
    assert not webhook.verify_signature(body, "sha256=abc", "s3cret")
    assert not webhook.verify_signature(body, good, "")                      # no secret configured: fail closed
