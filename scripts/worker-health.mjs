import pg from "pg";
import { loadRuntimeEnvironment } from "./runtime-config.mjs";

// Same private mount as the worker; the check needs no Auth administrator key.
const environment = loadRuntimeEnvironment(process.env, undefined, true);
const client = new pg.Client({ connectionString: environment.DATABASE_URL,
  connectionTimeoutMillis: 4000, query_timeout: 4000 });
try {
  await client.connect();
  const result = await client.query("SELECT EXISTS(SELECT 1 FROM app_os.worker_health WHERE id='main' AND last_seen > now() - interval '60 seconds') AS healthy");
  if (result.rows[0]?.healthy !== true) process.exitCode = 1;
} catch { process.exitCode = 1; }
finally { await client.end().catch(() => {}); }
