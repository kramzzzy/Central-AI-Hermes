// Shared SQL for the automatic installer and isolated integration fixtures.
import { readdir, readFile } from "node:fs/promises";

export const appRoleSQL = `REVOKE ALL ON SCHEMA app_os FROM PUBLIC,anon,authenticated;
DO $roles$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='app_os_api') THEN CREATE ROLE app_os_api NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE; END IF; END $roles$;
GRANT USAGE ON SCHEMA app_os TO app_os_api;
GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA app_os TO app_os_api;
GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA app_os TO app_os_api;
DO $policies$ DECLARE record record; BEGIN FOR record IN SELECT tablename FROM pg_tables WHERE schemaname='app_os' LOOP
  EXECUTE format('ALTER TABLE app_os.%I ENABLE ROW LEVEL SECURITY',record.tablename);
  IF NOT EXISTS(SELECT 1 FROM pg_policies WHERE schemaname='app_os' AND tablename=record.tablename AND policyname='app_api_only') THEN
    EXECUTE format('CREATE POLICY app_api_only ON app_os.%I TO app_os_api USING (true) WITH CHECK (true)',record.tablename);
  END IF;
END LOOP; END $policies$;`;

export async function supabaseAppSchema() {
  const files = (await readdir(new URL("../db/", import.meta.url)))
    .filter((name) => /^\d{3}-?.*\.sql$/.test(name))
    .sort();
  const chunks = await Promise.all(
    files.map((name) =>
      readFile(new URL("../db/" + name, import.meta.url), "utf8"),
    ),
  );
  // Source migrations are operator material. Frontend/data API startup never
  // receives a schema-owner URL or executes this SQL.
  return (
    "BEGIN;\nCREATE SCHEMA IF NOT EXISTS app_os;\nSET LOCAL search_path=app_os;\n" +
    chunks
      .map((text) => text.replace(/^\s*(BEGIN|COMMIT);\s*$/gm, ""))
      .join("\n") +
    "\n" + appRoleSQL + "\nCOMMIT;\nNOTIFY pgrst,'reload schema';\n"
  );
}
