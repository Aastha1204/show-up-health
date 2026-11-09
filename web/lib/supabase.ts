import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const URL = process.env.NEXT_PUBLIC_SUPABASE_URL;
const ANON = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

/** True when a Supabase project is configured (the ONLY secret allowed in the bundle is this anon key).
 *  Without it the app runs in local mode: dev sign-in + 3-second polling instead of Realtime. */
export const supabaseConfigured = Boolean(URL && ANON);

let client: SupabaseClient | null = null;
export function getSupabase(): SupabaseClient | null {
  if (!supabaseConfigured) return null;
  if (!client) client = createClient(URL!, ANON!, { auth: { persistSession: true, autoRefreshToken: true } });
  return client;
}
