import pg from "pg";
import { loadRuntimeEnvironment } from "./runtime-config.mjs";

let dbUrl = process.env.DATABASE_URL;
if (!dbUrl) {
  try {
    const environment = loadRuntimeEnvironment(process.env, undefined, true);
    dbUrl = environment?.DATABASE_URL;
  } catch {}
}

if (!dbUrl) {
  console.error("Worker healthcheck: DATABASE_URL is not set.");
  process.exit(1);
}

const client = new pg.Client({
  connectionString: dbUrl,
  connectionTimeoutMillis: 5000,
  query_timeout: 5000,
});

try {
  await client.connect();
  let result;
  try {
    result = await client.query(
      "SELECT EXISTS(SELECT 1 FROM worker_health WHERE id='main' AND last_seen > now() - interval '90 seconds') AS healthy",
    );
  } catch {
    result = await client.query(
      "SELECT EXISTS(SELECT 1 FROM app_os.worker_health WHERE id='main' AND last_seen > now() - interval '90 seconds') AS healthy",
    );
  }
  if (result?.rows?.[0]?.healthy !== true) {
    process.exitCode = 1;
  }
} catch (err) {
  console.warn("Worker healthcheck probe failed:", err?.message || err);
  process.exitCode = 1;
} finally {
  await client.end().catch(() => {});
}
