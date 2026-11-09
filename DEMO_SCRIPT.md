# The 3-minute demo

**Pitch:** patients confirm, reschedule or cancel with one tap on WhatsApp, freed slots go to the waitlist, and the owner sees no-shows fall and what that is worth. Fictional clinic: SmileCare Dental Clinic, Kalyan West.

## Before the call (2 minutes)
1. API and web running, signed in as **owner** (`owner@smilecare.demo`; passwords in the README, or the local sign-in buttons).
2. Open **Demo** (the presenter view). Press **Reset demo**: fresh data for today, the seeded "cancel me" appointment and the waitlist restored. (Reset keeps logins and the messaging mode.)
3. Check the badge at top right: **Simulator** (safe) or **Live WhatsApp**. For a live prospect, run *Settings > WhatsApp > Re-check* first: it must say **Ready for Live**, and templates should be approved.
4. Set **Offer lasts: 2 min** if you want to show an offer expiring; otherwise leave 15.
5. Closed on Sundays: the page opens on the next working day by itself, and "tomorrow" means the next working day.

## The script
| Time | You do | They see |
|---|---|---|
| 0:00 | **1 · Add the prospect.** Type their name and their own mobile number, language, **Book for tomorrow**. | Their appointment appears on the schedule (right). The confirmation lands on their phone (Live) and in the phone on the left (Simulator or Live mirror). |
| 0:40 | **2 · Fire a reminder → 24-hour reminder.** | The reminder with **Confirm / Reschedule / Cancel** buttons. |
| 1:00 | They tap **Reschedule** on their own phone (in Simulator, you tap it on the left), open **See slots**, pick a time, **Send**. | A fresh confirmation with the new time. |
| 1:30 | Point at the schedule. | **Their card turned blue (Rescheduled) by itself**, within seconds, no refresh. A toast says what happened. |
| 1:45 | **3 · Cancel the seeded appointment** ("Cancel it & offer the slot"). | The slot is offered to the next waitlisted patient. The phone jumps to *their* chat: "a slot has opened…" with **Book / Not now**. |
| 2:15 | Tap **Book** in that chat. | The gap on the schedule fills (green card, waitlist icon); the confirmation arrives. |
| 2:30 | **Analytics →** | "Slots recovered" went **18 → 19**. Read the ROI card: *"N saved appointments × ₹1,200 = ₹… vs a ₹2,999 plan."* Close: **"Four saved appointments a month pays for this system."** |

## If something goes wrong
- **WhatsApp is slow or the network drops:** click **Simulator** in *4 · Mode, summary, reset*. Everything continues in the phone on the left, with the same buttons and the same effects on the schedule. Say: "same system, I'm just showing you the patient's side here." Flip back to Live afterwards if you like.
- **The prospect's message does not arrive (Live):** open the appointment's *Message log* (Today > click the card): it shows the status and Meta's error code with a plain-language reason (e.g. the 24-hour window, a number not on the test recipient list, a template not approved).
- **No offer was sent after cancelling:** offers are not sent during quiet hours (21:00-08:00), or when nobody on the waitlist matches. The toast says which. Reset and retry in working hours.
- **Seeded appointment already used:** press **Reset demo**.
- **Show the doctor's morning summary** at any point: *Send 8 a.m. summary* (jumps to the doctor's chat).

## Facts worth knowing for questions
- Messages never mention treatments, conditions or specialty (a guard blocks the send otherwise).
- Patients who reply **STOP** receive nothing more, ever.
- Seeded patients can never receive a real WhatsApp message, even in Live mode.
- Everything shown on Analytics from seeded patients is labelled **Simulated data**.

## Optional 30-second extras (only if the prospect asks)
- **Hindi/Marathi:** set a patient's language to Hindi in the form before step 1; the reminder arrives in Hindi with Hindi buttons. Say that the wording should be reviewed by someone who speaks it natively before real use.
- **Deposit:** open an appointment card > *Request deposit (₹500)*. In Simulator mode press *Simulate: patient paid* and the card shows "₹500 paid". Say plainly that real payments need their Razorpay account.
- **Doctor's calendar:** Settings > doctor > *Calendar link*. Mention that it is a read-only feed that Google refreshes every few hours, not instant sync.
