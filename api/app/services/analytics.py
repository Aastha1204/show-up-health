"""Owner analytics. Every number is computed from the clinic's own appointments; nothing is invented.

Definitions (also shown in the UI):
  no-show rate        = no-shows / (arrived + no-shows)            (cancellations are not counted either way)
  slots recovered     = non-cancelled appointments booked from the waitlist (recovered_via_waitlist)
  revenue saved       = slots recovered x the clinic's average ticket
  confirmation rate   = of appointments that reached a settled state (confirmed / rescheduled / arrived / no-show),
                        the share the PATIENT confirmed on WhatsApp (not phone confirmations by reception)
  saved appointments  = slots recovered + no-shows avoided vs the clinic's baseline rate (an ESTIMATE), over the
                        last 30 days plus bookings already made for the next 7
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from ..timeutil import ist_to_utc, ist_today, to_ist

PLAN_PRICE_INR = 2999
WEEKS = 13
SETTLED = ("confirmed", "rescheduled", "arrived", "no_show")


def month_bounds(month: str | None, now: datetime) -> tuple[date, datetime, datetime]:
    if month:
        y, m = (int(x) for x in month.split("-"))
    else:
        t = ist_today(now)
        y, m = t.year, t.month
    first = date(y, m, 1)
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return first, ist_to_utc(first, time(0)), ist_to_utc(nxt, time(0))


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


async def _one(conn, sql, args):
    return await (await conn.execute(sql, args)).fetchone()


def _rate(no_shows: int, completed: int) -> float | None:
    return round(no_shows / completed, 4) if completed else None


async def compute(conn, clinic: dict, month: str | None, now: datetime) -> dict:
    first, lo, hi = month_bounds(month, now)
    cid = clinic["id"]
    ticket = clinic["avg_ticket_value"]

    t = await _one(conn, """
        select count(*) filter (where status='arrived') as arrived,
               count(*) filter (where status='no_show') as no_shows,
               count(*) filter (where recovered_via_waitlist and status <> 'cancelled') as recovered,
               count(*) filter (where status = any(%s)) as settled,
               count(*) filter (where status = any(%s) and exists (
                   select 1 from appointment_event e where e.appointment_id = a.id
                   and e.actor = 'patient' and e.to_status = 'confirmed')) as confirmed_via_whatsapp
        from appointment a where clinic_id=%s and start_at >= %s and start_at < %s""", [list(SETTLED), list(SETTLED), cid, lo, hi])
    completed = t["arrived"] + t["no_shows"]

    since = ist_to_utc(clinic["go_live_date"], time(0)) if clinic["go_live_date"] else None
    recovered_total = (await _one(conn, """select count(*) as n from appointment where clinic_id=%s and recovered_via_waitlist
                                           and status <> 'cancelled' and (%s::timestamptz is null or start_at >= %s)""",
                                  [cid, since, since]))["n"]

    # weekly series: the last 13 IST weeks (Monday start), oldest first, zero-filled
    this_monday = monday_of(ist_today(now))
    weeks = [this_monday - timedelta(weeks=i) for i in range(WEEKS - 1, -1, -1)]
    w_lo, w_hi = ist_to_utc(weeks[0], time(0)), ist_to_utc(this_monday + timedelta(days=7), time(0))
    rows = await (await conn.execute("""
        select (date_trunc('week', a.start_at at time zone 'Asia/Kolkata'))::date as week_start,
               count(*) filter (where status in ('arrived','no_show')) as completed,
               count(*) filter (where status = 'no_show') as no_shows,
               count(*) filter (where recovered_via_waitlist and status <> 'cancelled') as recovered
        from appointment a where clinic_id=%s and start_at >= %s and start_at < %s group by 1""", [cid, w_lo, w_hi])).fetchall()
    by_week = {r["week_start"]: r for r in rows}
    weekly = []
    for w in weeks:
        r = by_week.get(w, {"completed": 0, "no_shows": 0, "recovered": 0})
        weekly.append({"week_start": w, "completed": r["completed"], "no_shows": r["no_shows"],
                       "no_show_rate": _rate(r["no_shows"], r["completed"]), "recovered": r["recovered"]})

    # how much of what is shown comes from seeded (simulated) patients
    sim = await _one(conn, """select count(*) filter (where p.is_demo_seed) as seeded, count(*) as total
                              from appointment a join patient p on p.id = a.patient_id
                              where a.clinic_id=%s and a.start_at >= %s and a.start_at < %s""", [cid, w_lo, w_hi])
    sim_share = round(sim["seeded"] / sim["total"], 4) if sim["total"] else 0.0

    # ROI: last 30 days + the next 7
    r_lo, r_hi = now - timedelta(days=30), now + timedelta(days=7)
    roi_row = await _one(conn, """
        select count(*) filter (where status in ('arrived','no_show')) as completed,
               count(*) filter (where status = 'no_show') as no_shows,
               count(*) filter (where recovered_via_waitlist and status <> 'cancelled') as recovered
        from appointment where clinic_id=%s and start_at >= %s and start_at < %s""", [cid, r_lo, r_hi])
    roi_rate = _rate(roi_row["no_shows"], roi_row["completed"])
    baseline = float(clinic["baseline_no_show_rate"])
    avoided = max(0, int((baseline - roi_rate) * roi_row["completed"])) if roi_rate is not None else 0
    saved = roi_row["recovered"] + avoided

    return {
        "month": f"{first.year}-{first.month:02d}",
        "simulated_share": sim_share,
        "clinic": {"baseline_no_show_rate": baseline, "avg_ticket_value": ticket,
                   "go_live_date": clinic["go_live_date"], "plan_price": PLAN_PRICE_INR},
        "tiles": {
            "no_show_rate": _rate(t["no_shows"], completed), "completed": completed, "no_shows": t["no_shows"],
            "recovered_slots": t["recovered"], "recovered_since_go_live": recovered_total,
            "revenue_saved": t["recovered"] * ticket,
            "confirmation_rate": round(t["confirmed_via_whatsapp"] / t["settled"], 4) if t["settled"] else None,
            "confirmed_via_whatsapp": t["confirmed_via_whatsapp"], "settled": t["settled"],
        },
        "weekly": weekly,
        "roi": {"recovered": roi_row["recovered"], "avoided_no_shows": avoided, "saved_appointments": saved,
                "value": saved * ticket, "plan_price": PLAN_PRICE_INR, "no_show_rate": roi_rate,
                "window_from": to_ist(r_lo).date(), "window_to": to_ist(r_hi).date()},
    }
