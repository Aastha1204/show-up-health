/** Payloads in Meta's documented WhatsApp Cloud API webhook shape: what the Simulator "phone" sends when tapped.
 *  They go to /simulator/inbound and run through the same handler as the real /webhooks/whatsapp. */

const waId = (phone: string) => phone.replace(/^\+/, "");
const newId = () => "wamid.IN" + Array.from(crypto.getRandomValues(new Uint8Array(12)), (b) => b.toString(16).padStart(2, "0")).join("").toUpperCase();

function wrap(phone: string, message: Record<string, unknown>, contextWamid?: string | null) {
  const msg = {
    from: waId(phone),
    id: newId(),
    timestamp: String(Math.floor(Date.now() / 1000)),
    ...message,
    ...(contextWamid ? { context: { from: "15550000000", id: contextWamid } } : {}),
  };
  return {
    object: "whatsapp_business_account",
    entry: [{
      id: "WABA_ID",
      changes: [{
        field: "messages",
        value: {
          messaging_product: "whatsapp",
          metadata: { display_phone_number: "15550000000", phone_number_id: "SIMULATOR" },
          contacts: [{ profile: { name: "Patient" }, wa_id: waId(phone) }],
          messages: [msg],
        },
      }],
    }],
  };
}

/** Template quick-reply tap: type "button". */
export const buttonTap = (phone: string, payload: string, text: string, contextWamid?: string | null) =>
  wrap(phone, { type: "button", button: { payload, text } }, contextWamid);

/** Row chosen in an interactive list. */
export const listReply = (phone: string, id: string, title: string, description: string, contextWamid?: string | null) =>
  wrap(phone, { type: "interactive", interactive: { type: "list_reply", list_reply: { id, title, description } } }, contextWamid);

/** Free text, including STOP. */
export const textMessage = (phone: string, body: string) => wrap(phone, { type: "text", text: { body } });
