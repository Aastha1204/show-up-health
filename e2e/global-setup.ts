import { execFileSync } from "node:child_process";
import path from "node:path";

/** Re-seed the demo database (relative to the current IST time) so each run starts from identical data.
 *  Set E2E_SKIP_SEED=1 to keep whatever is in the database (e.g. after adding a custom appointment by hand). */
export default async function globalSetup() {
  if (process.env.E2E_SKIP_SEED) return;
  const python = process.env.E2E_PYTHON ?? "python";
  const out = execFileSync(python, ["-m", "app.seed"], { cwd: path.resolve(__dirname, "../api"), encoding: "utf-8" });
  console.log(out.trim());
}
