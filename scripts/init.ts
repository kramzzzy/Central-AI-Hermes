import { getMigrations } from "better-auth/db/migration";
import { readFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
// Apply the schema-owner URL only in this short-lived migration process.
// Dynamic imports ensure auth and application queries use the same connection.
if (process.env.DATABASE_MIGRATION_URL) {
  process.env.DATABASE_URL = process.env.DATABASE_MIGRATION_URL;
}
const { db, transaction } = await import("../lib/db");
const expectedSchema = process.env.DATABASE_SCHEMA || "public";
const {
  rows: [target],
} = await db.query<{ schema: string | null }>(
  "SELECT current_schema() AS schema",
);
if (target.schema !== expectedSchema) {
  await db.end();
  throw new Error(
    `Create DATABASE_SCHEMA (${expectedSchema}) before running migrations.`,
  );
}
const { auth } = await import("../lib/auth");
const migration = await getMigrations(auth.options);
await migration.runMigrations();
await db.query(
  await readFile(new URL("../db/001.sql", import.meta.url), "utf8"),
);
await db.query(
  await readFile(new URL("../db/002-access.sql", import.meta.url), "utf8"),
);
await db.query(
  await readFile(new URL("../db/003-voice.sql", import.meta.url), "utf8"),
);
await db.query(
  await readFile(new URL("../db/004-realtime.sql", import.meta.url), "utf8"),
);
await db.query(
  await readFile(
    new URL("../db/005-task-attachments.sql", import.meta.url),
    "utf8",
  ),
);
await db.query(
  await readFile(
    new URL("../db/006-team-assistants.sql", import.meta.url),
    "utf8",
  ),
);
await db.query(
  await readFile(
    new URL("../db/007-google-actions.sql", import.meta.url),
    "utf8",
  ),
);
await db.query(
  await readFile(new URL("../db/008-calendar.sql", import.meta.url), "utf8"),
);
for (const name of ['009-specialists.sql','010-routines.sql','011-memory-controls.sql','012-installation.sql','013-files.sql','014-call-conversation.sql','015-supabase-auth.sql','016-contacts.sql','017-auth-otp.sql','018-access-requests.sql']) {
  await db.query(await readFile(new URL('../db/'+name,import.meta.url),'utf8'));
}
if (process.env.OWNER_EMAIL && process.env.OWNER_PASSWORD) {
  const ctx = await auth.$context;
  const hash = await ctx.password.hash(process.env.OWNER_PASSWORD);
  await transaction(async (c) => {
    await c.query("SELECT pg_advisory_xact_lock(987165)");
    if ((await c.query("SELECT 1 FROM memberships LIMIT 1")).rowCount) return;
    const user = randomUUID(),
      org = randomUUID();
    await c.query(
      'INSERT INTO "user" (id,name,email,"emailVerified","createdAt","updatedAt") VALUES($1,$2,$3,true,now(),now())',
      [user, "Workspace owner", process.env.OWNER_EMAIL],
    );
    await c.query(
      'INSERT INTO account(id,"accountId","providerId","userId",password,"createdAt","updatedAt") VALUES($1,$2,$3,$2,$4,now(),now())',
      [randomUUID(), user, "credential", hash],
    );
    await c.query("INSERT INTO organizations(id,name) VALUES($1,$2)", [
      org,
      "Your workspace",
    ]);
    await c.query(
      "INSERT INTO memberships(user_id,org_id,role) VALUES($1,$2,$3)",
      [user, org, "owner"],
    );
    await c.query("INSERT INTO audit(org_id,actor,action) VALUES($1,$2,$3)", [
      org,
      user,
      "Workspace created",
    ]);
  });
}
// Preserve installed owners, agents and environment bindings; never seed a personality.
await db.query(`UPDATE installation_state i SET org_id=m.org_id,owner_id=m.user_id,phase='complete',completed_at=now(),
  assistant_id=(SELECT id FROM agent_profiles WHERE org_id=m.org_id AND is_main LIMIT 1),
  backend_verified=true,assistant_verified=true
  FROM memberships m WHERE i.id AND i.org_id IS NULL AND m.role='owner' AND m.status='active'
  AND ($1::text IS NULL OR m.org_id::text=$1) AND ($2::text IS NULL OR m.user_id=$2)`,[process.env.HERMES_CHAT_ORG_ID || null,process.env.HERMES_OWNER_USER_ID || null]);
console.log("Database migrations complete.");
await db.end();
