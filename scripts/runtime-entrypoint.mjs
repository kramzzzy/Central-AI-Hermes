import { spawn } from "node:child_process";
import { loadRuntimeEnvironment } from "./runtime-config.mjs";

const worker = process.argv[2] === "worker";
let environment;
try {
  environment = loadRuntimeEnvironment(process.env, undefined, worker);
} catch (error) {
  console.error(error.message);
  process.exit(1);
}
const arguments_ = worker
  ? ["node_modules/tsx/dist/cli.mjs", "scripts/worker.ts"]
  : ["node_modules/next/dist/bin/next", "start"];
const child = spawn(process.execPath, arguments_, {
  env: environment,
  stdio: "inherit",
});
for (const signal of ["SIGTERM", "SIGINT"])
  process.on(signal, () => child.kill(signal));
child.on("exit", (code, signal) => process.exit(code ?? (signal ? 1 : 0)));
child.on("error", () => {
  console.error("Application process failed to start.");
  process.exit(1);
});
