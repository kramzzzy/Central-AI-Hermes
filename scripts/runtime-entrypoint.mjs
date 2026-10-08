import { spawn, spawnSync } from "node:child_process";
import { loadRuntimeEnvironment } from "./runtime-config.mjs";

const worker = process.argv[2] === "worker";
let environment;
try {
  environment = loadRuntimeEnvironment(process.env, undefined, worker);
} catch (error) {
  console.error(error.message);
  process.exit(1);
}

if (!worker && environment.DATABASE_URL) {
  try {
    console.log("Ensuring database schema is initialized...");
    spawnSync(
      process.execPath,
      ["node_modules/tsx/dist/cli.mjs", "scripts/init.ts"],
      {
        env: environment,
        stdio: "inherit",
      },
    );
  } catch (err) {
    console.warn("Database initialization warning:", err?.message || err);
  }
}

const processes = [];

if (worker) {
  const workerProc = spawn(
    process.execPath,
    ["node_modules/tsx/dist/cli.mjs", "scripts/worker.ts"],
    { env: environment, stdio: "inherit" },
  );
  processes.push(workerProc);
} else {
  // Start Next.js Web Server
  const webProc = spawn(
    process.execPath,
    ["node_modules/next/dist/bin/next", "start"],
    { env: environment, stdio: "inherit" },
  );
  processes.push(webProc);

  // Start background agent task worker concurrently unless explicitly disabled
  if (process.env.DISABLE_BACKGROUND_WORKER !== "1" && environment.DATABASE_URL) {
    console.log("Starting embedded background agent worker...");
    const workerProc = spawn(
      process.execPath,
      ["node_modules/tsx/dist/cli.mjs", "scripts/worker.ts"],
      { env: environment, stdio: "inherit" },
    );
    processes.push(workerProc);
  }
}

for (const signal of ["SIGTERM", "SIGINT"]) {
  process.on(signal, () => {
    for (const p of processes) {
      try { p.kill(signal); } catch {}
    }
  });
}

processes[0].on("exit", (code, signal) => {
  for (const other of processes.slice(1)) {
    try { other.kill("SIGTERM"); } catch {}
  }
  process.exit(code ?? (signal ? 1 : 0));
});

processes[0].on("error", (err) => {
  console.error("Application process failed to start:", err);
  process.exit(1);
});

