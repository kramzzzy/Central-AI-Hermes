// Public source package with per-file hashes. Private install mounts are excluded.
import { readdir, mkdir, copyFile, readFile, writeFile, lstat } from "node:fs/promises";
import { join, resolve, relative } from "node:path";
import { createHash, randomUUID } from "node:crypto";
import { spawnSync } from "node:child_process";
const root = resolve("."), output = join(root, ".runtime", "app-os-releases", randomUUID());
const packageRoot = join(output, "app-os");
const folders = ["app", "components", "lib", "scripts", "db", "public", "hermes_plugins"];
const files = ["Dockerfile", "compose.yaml", "compose.setup.yaml", "deploy/setup/Dockerfile", "deploy/setup/auth.Dockerfile", ".dockerignore", ".env.example", "package.json",
  "package-lock.json", "tsconfig.json", "next.config.ts", "proxy.ts",
  "README.md", "deploy/hermes/Dockerfile", "deploy/laya/Dockerfile",
  "docs/INSTALLATION_GUIDE.md", "docs/system_setup_implementation_plan.md",
  "docs/central_ai_mcp_specification.md", "deploy/README.md"];
files.push("deploy/whatsapp/Dockerfile.caller",
  "deploy/whatsapp/meowcaller.patch", "deploy/whatsapp/LICENSE.meowcaller", "deploy/hindsight/Dockerfile");
const hashes = {};
function excluded(path) {
  return /(^|\/)(?:\.env(?:\..*)?|\.runtime|\.git|__pycache__|node_modules|tests|test-results)(\/|$)/.test(path) ||
    /\.(?:pyc|clixml|tsbuildinfo|log)$/.test(path) ||
    /(^|\/)(?:test_|.*-fixture\.|.*-integration\.|.*-smoke\.)/.test(path) ||
    (path.startsWith("scripts/whatsapp-caller/") && !path.endsWith(".go"));
}
async function copy(path) {
  const source = join(root, path), metadata = await lstat(source);
  if (metadata.isSymbolicLink()) throw new Error("Release sources must not contain symlinks: " + path);
  if (metadata.isDirectory()) {
    for (const item of (await readdir(source)).sort()) {
      const nested = path + "/" + item;
      if (!excluded(nested)) await copy(nested);
    }
  } else {
    const content = await readFile(source);
    const destination = join(packageRoot, path);
    await mkdir(resolve(destination, ".."), {recursive: true});
    await copyFile(source, destination);
    hashes[path] = createHash("sha256").update(content).digest("hex");
  }
}
await mkdir(packageRoot, {recursive: true});
for (const path of [...folders, ...files]) await copy(path);
await writeFile(join(packageRoot, "app-os.release.json"), JSON.stringify({
  format: 1, plugin: "central-ai-app-os", version: "1.0.0",
  hermes_sdk_image: "nousresearch/hermes-agent:v2026.9.24@sha256:fca358f12efd65bfaaca05884166f15c0e2788375ca30d77061ac1ebc96452b7",
  services: ["web", "app-api", "app-worker"], files: hashes,
  coolify_compose: "compose.yaml",
  backend_services: ["hermes", "caller", "voice", "hindsight", "laya"],
  meowcaller_revision: "c48c3e2a243c672c942cbf0c941d14161720d540",
}, null, 2) + "\n");
const archive = join(output, "app-os-1.0.0.tar.gz");
const tar = spawnSync("tar", ["-czf", archive, "-C", output, "app-os"], {encoding: "utf8"});
if (tar.error || tar.status !== 0) throw new Error("Could not create the release archive.");
const digest = createHash("sha256").update(await readFile(archive)).digest("hex");
await writeFile(archive + ".sha256", digest + "  app-os-1.0.0.tar.gz\n");
console.log(JSON.stringify({archive: relative(root, archive), sha256: digest, files: Object.keys(hashes).length}));
