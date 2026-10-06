import { db, transaction } from "../lib/db";
import { runHermes, cancelHermesRun } from "../lib/hermes";
import { assertRunAccess } from "../lib/run-access";
import { syncSharedCalendars } from "../lib/team-calendar";
import {tickRoutines} from "../lib/routines";
import {nativeRequest} from "../lib/native-library";
let stopping = false;
process.on("SIGTERM", () => {
  stopping = true;
});
process.on("SIGINT", () => {
  stopping = true;
});
async function pulse() {
  await db.query(
    "INSERT INTO worker_health(id) VALUES('main') ON CONFLICT(id) DO UPDATE SET last_seen=now()",
  );
}
const timer = setInterval(() => {
  void pulse().catch(() => {});
}, 5000);
const calendarTimer = setInterval(() => {
  void syncSharedCalendars().catch(() => console.error("calendar_sync_failed"));
}, 60000);
void syncSharedCalendars().catch(() => console.error("calendar_sync_failed"));
const routinesTimer=setInterval(()=>{void tickRoutines().catch(()=>console.error('routine_tick_failed'));},60000);
void tickRoutines().catch(()=>console.error('routine_tick_failed'));
const memoryTimer=setInterval(()=>{void nativeRequest('/memory/maintenance',{}).catch(()=>console.error('memory_maintenance_failed'));},3600000);
console.log("Agent worker started.");
let lastCleanup = 0;
while (!stopping) {
  try {
    if (Date.now() - lastCleanup > 3600000) {
      await db.query(
        "DELETE FROM access_rate_limits WHERE window_start<now()-interval '2 days'",
      );
      lastCleanup = Date.now();
    }
    await pulse();
    await transaction(async (c) => {
      const stale = await c.query(
        "UPDATE runs SET status='failed',error='Worker interrupted. Review before retrying.',finished_at=now() WHERE status='running' AND heartbeat_at<now()-interval '6 minutes' RETURNING task_id",
      );
      for (const row of stale.rows)
        await c.query("UPDATE tasks SET status='failed' WHERE id=$1", [
          row.task_id,
        ]);
    });
    const run = await transaction(async (c) => {
      const item = (
        await c.query(
          "SELECT * FROM runs WHERE status='queued' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1",
        )
      ).rows[0];
      if (!item) return null;
      await c.query(
        "UPDATE runs SET status='running',started_at=now(),heartbeat_at=now() WHERE id=$1",
        [item.id],
      );
      await c.query("UPDATE tasks SET status='running' WHERE id=$1", [
        item.task_id,
      ]);
      return item;
    });
    if (!run) {
      if (process.env.WORKER_ONCE === "1") break;
      await new Promise((r) => setTimeout(r, 1500));
      continue;
    }
    let output: string | null = null,
      error: string | null = null;
    const controller = new AbortController();
    let checking = false;
    const accessTimer = setInterval(async () => {
      if (checking || controller.signal.aborted) return;
      checking = true;
      try {
        await assertRunAccess(db, run.id, run.org_id);
      } catch {
        controller.abort();
        await cancelHermesRun(run.id);
      } finally {
        checking = false;
      }
    }, 2000);
    try {
      await assertRunAccess(db, run.id, run.org_id);
      const files = (
        await db.query(
          "SELECT name,mime,content FROM run_attachments WHERE run_id=$1",
          [run.id],
        )
      ).rows.map((f) => ({
        name: f.name,
        mime: f.mime,
        data: f.content.toString("base64"),
      }));
      output = await runHermes(
        run.instruction,
        run.knowledge,
        run.id,
        run.agent_config,
        controller.signal,
        files,
      );
      await assertRunAccess(db, run.id, run.org_id);
    } catch (e) {
      output = null;
      error = controller.signal.aborted
        ? "Run stopped because access changed or could not be verified."
        : e instanceof Error
          ? e.message
          : "Agent run failed.";
      await cancelHermesRun(run.id);
    } finally {
      clearInterval(accessTimer);
    }
    await transaction(async (c) => {
      await c.query("SELECT id FROM organizations WHERE id=$1 FOR UPDATE", [
        run.org_id,
      ]);
      try {
        await assertRunAccess(c, run.id, run.org_id);
      } catch {
        output = null;
        error = "Run stopped because workspace or agent access changed.";
      }
      const status = error ? "failed" : "review";
      await c.query(
        "UPDATE runs SET status=$1,output=$2,error=$3,finished_at=now() WHERE id=$4",
        [status, output, error, run.id],
      );
      await c.query("UPDATE tasks SET status=$1 WHERE id=$2", [
        status,
        run.task_id,
      ]);
      await c.query(
        "INSERT INTO audit(org_id,actor,action,entity_id) VALUES($1,$2,$3,$4)",
        [
          run.org_id,
          "Hermes",
          error
            ? "Agent run failed"
            : run.agent_config?.tool_access === "all"
              ? "Agent response ready for review"
              : "Draft ready for review",
          run.id,
        ],
      );
    });
    if (process.env.WORKER_ONCE === "1") break;
  } catch (e) {
    console.error(
      "worker_cycle_failed",
      e instanceof Error ? e.name : "Unknown",
    );
    if (process.env.WORKER_ONCE === "1") {
      process.exitCode = 1;
      break;
    }
    await new Promise((r) => setTimeout(r, 3000));
  }
}
clearInterval(timer);
clearInterval(calendarTimer);
clearInterval(routinesTimer);
clearInterval(memoryTimer);
await db.end();
