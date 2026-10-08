// First-install provisioning only. Runs in Coolify; no host scripts or old state.
import {
  readFile,
  writeFile,
  mkdir,
  lstat,
  rename,
  chown,
  chmod,
  readdir,
} from "node:fs/promises";
import { join, resolve } from "node:path";
import { randomBytes, randomUUID, createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import YAML from "yaml";
import {
  validateFrontendEnvironment,
  loadRuntimeEnvironment,
} from "./runtime-config.mjs";
import { appRoleSQL } from "./prepare-supabase-app-schema.mjs";

import { whatsappRouting } from './whatsapp-routing.mjs';

const secret = () => randomBytes(32).toString("hex");
export const profileDefinitions = [
  {
    name: "Leo",
    native_profile: "leo",
    role: "Personal and business assistant",
    description:
      "Your private assistant for communication, planning and business tasks.",
    artwork: "leo",
    instructions:
      "You are Leo, the owner’s private assistant on Central AI. Use live tools for current facts. Keep each person’s conversation and memory private. Never invent reports, progress or completed actions. Request required approvals before external actions. Use clear Australian English, with general English as a fallback.",
  },
  {
    name: "Sarah",
    native_profile: "sarah",
    role: "Social media assistant",
    description:
      "Helps prepare social content, campaign plans and approved drafts.",
    artwork: "social",
    instructions:
      "You are Sarah, a social media assistant on Central AI. Draft and analyse social content using the member’s authorised information. Publishing and external actions require the configured approval flow. Never claim a draft has been published. Keep each member’s memory and account connections private.",
  },
];
export function installationSettings(env) {
  validateFrontendEnvironment({
    APP_RUNTIME: "frontend",
    APP_ORIGIN: env.APP_ORIGIN,
    NEXT_PUBLIC_SUPABASE_URL: env.NEXT_PUBLIC_SUPABASE_URL,
    NEXT_PUBLIC_SUPABASE_ANON_KEY: env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
  });
  for (const name of [
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_BOOTSTRAP_DATABASE_URL",
    "SERVICE_PASSWORD_SETUP",
    "OPENROUTER_API_KEY",
  ]) {
    if (
      typeof env[name] !== "string" ||
      !env[name].trim() ||
      /[\r\n\0]/.test(env[name])
    )
      throw new Error("Set " + name + " in Coolify before deployment.");
  }
  if (env.SERVICE_PASSWORD_SETUP.length < 32)
    throw new Error(
      "SERVICE_PASSWORD_SETUP must contain at least 32 characters.",
    );
  const db = new URL(env.SUPABASE_BOOTSTRAP_DATABASE_URL);
  if (
    !["postgres:", "postgresql:"].includes(db.protocol) ||
    !db.username ||
    db.username === "app_os_api" ||
    !db.password ||
    !db.hostname ||
    db.pathname === "/"
  )
    throw new Error(
      "SUPABASE_BOOTSTRAP_DATABASE_URL must be the private Supabase operator connection.",
    );
  const settings = Object.fromEntries(
    [
      "APP_ORIGIN",
      "NEXT_PUBLIC_SUPABASE_URL",
      "NEXT_PUBLIC_SUPABASE_ANON_KEY",
      "SUPABASE_SERVICE_ROLE_KEY",
      "SUPABASE_BOOTSTRAP_DATABASE_URL",
      "SERVICE_PASSWORD_SETUP",
      "OPENROUTER_API_KEY",
      "FISH_API_KEY",
    ].map((k) => [k, env[k]]),
  );
  // Deployment preferences, never credentials in the app or persona template.
  settings.chatModel = env.HERMES_CHAT_MODEL || "openai/gpt-6.1-sol";
  settings.chatProvider = env.HERMES_CHAT_PROVIDER || 'openrouter';
  const providerKeys = {openrouter:'OPENROUTER_API_KEY',openai:'OPENAI_API_KEY',anthropic:'ANTHROPIC_API_KEY',gemini:'GEMINI_API_KEY','openai-codex':null};
  if (!(settings.chatProvider in providerKeys)) throw new Error('Use a provider supported by the setup wizard.');
  for (const key of ['OPENAI_API_KEY','ANTHROPIC_API_KEY','GEMINI_API_KEY']) {
    settings[key] = env[key] || '';
    if (/[\r\n\0]/.test(settings[key])) throw new Error('Set a valid provider API key.');
  }
  const selectedKey = providerKeys[settings.chatProvider];
  if (selectedKey && !settings[selectedKey]?.trim()) throw new Error('Set '+selectedKey+' for the selected assistant provider.');
  settings.voiceModel = env.HERMES_VOICE_MODEL || "openai/gpt-4.1-mini";
  const speechDefault = env.FISH_API_KEY?.trim() ? "fish" : "piper";
  settings.speechProvider = env.HERMES_CALL_SPEECH || env.CENTRAL_AI_CALL_SPEECH || speechDefault;
  if (!["piper", "fish"].includes(settings.speechProvider))
    throw new Error("HERMES_CALL_SPEECH must be piper or fish.");
  settings.FISH_API_KEY = env.FISH_API_KEY || "";
  settings.fishVoiceId =
    env.FISH_VOICE_ID || "612b878b113047d9a770c069c8b4fdfe";
  if (!/^[a-fA-F0-9]{32}$/.test(settings.fishVoiceId))
    throw new Error("Set a valid Fish Voice ID.");
  if (
    /[\r\n\0]/.test(settings.FISH_API_KEY) ||
    (settings.speechProvider === "fish" && !settings.FISH_API_KEY.trim())
  )
    throw new Error("Set FISH_API_KEY when selecting Fish speech.");
  for (const value of [settings.chatModel, settings.voiceModel])
    if (!/^[a-zA-Z0-9_./:-]{1,160}$/.test(value))
      throw new Error("Use a provider model identifier.");
  settings.whatsapp = whatsappRouting(env);
  return settings;
}
async function directory(path, uid) {
  await mkdir(path, { recursive: true, mode: 0o700 });
  const info = await lstat(path);
  if (!info.isDirectory() || info.isSymbolicLink())
    throw new Error("Installation storage must be a regular directory.");
  if (process.platform === "linux" && process.getuid() === 0)
    await chown(path, uid, uid);
  await chmod(path, 0o700);
}
async function atomic(path, content, uid) {
  try {
    if ((await lstat(path)).isSymbolicLink())
      throw new Error("Unsafe installation file.");
  } catch (e) {
    if (e.code !== "ENOENT") throw e;
  }
  const temporary = path + "." + randomUUID() + ".tmp";
  await writeFile(
    temporary,
    typeof content === "string"
      ? content
      : JSON.stringify(content, null, 2) + "\n",
    { mode: 0o600, flag: "wx" },
  );
  if (process.platform === "linux" && process.getuid() === 0)
    await chown(temporary, uid, uid);
  await rename(temporary, path);
}
async function profileEnvironment(path, values) {
  let original = "";
  try {
    const info = await lstat(path);
    if (!info.isFile() || info.isSymbolicLink())
      throw new Error("Unsafe native credential file.");
    original = await readFile(path, "utf8");
  } catch (e) {
    if (e.code !== "ENOENT") throw e;
  }
  const keys = new Set(Object.keys(values));
  const kept = original.split(/\r?\n/).filter((line) => {
    const match = line.match(/^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=/);
    return !match || !keys.has(match[1]);
  });
  await atomic(
    path,
    kept.filter(Boolean).join("\n") +
      "\n" +
      Object.entries(values)
        .map(([key, value]) => key + "=" + JSON.stringify(value))
        .join("\n") +
      "\n",
    10000,
  );
}
export async function installationState(root) {
  const path = join(root, "installer", "state.json");
  await directory(join(root, "installer"), 0);
  try {
    const info = await lstat(path);
    if (!info.isFile() || info.isSymbolicLink())
      throw new Error("Unsafe installation state.");
    const state = JSON.parse(await readFile(path, "utf8"));
    if (
      state.version !== 1 ||
      !/^[0-9a-f-]{36}$/.test(state.id) ||
      !Array.isArray(state.agents) ||
      state.agents.length !== 2 ||
      state.agents.some(
        (a, i) =>
          a.name !== profileDefinitions[i].name ||
          a.native_profile !== profileDefinitions[i].native_profile ||
          !/^[0-9a-f-]{36}$/.test(a.id),
      ) ||
      Object.keys(state.keys || {})
        .sort()
        .join(",") !==
        [
          "database",
          "gateway",
          "hermes",
          "hindsight",
          "hindsightUI",
          "laya",
          "pairing",
          "voice",
          "fishBridge",
        ]
          .sort()
          .join(",") ||
      Object.values(state.keys).some((k) => !/^[0-9a-f]{64}$/.test(k))
    )
      throw new Error(
        "Installation state needs administrator repair; it was not reset.",
      );
    return state;
  } catch (e) {
    if (e.code !== "ENOENT") throw e;
  }
  const state = {
    version: 1,
    id: randomUUID(),
    agents: profileDefinitions.map((a) => ({ ...a, id: randomUUID() })),
    keys: Object.fromEntries(
      [
        "database",
        "gateway",
        "hermes",
        "hindsight",
        "hindsightUI",
        "laya",
        "pairing",
        "voice",
        "fishBridge",
      ].map((k) => [k, secret()]),
    ),
  };
  // Persist before any database change. A failed/retried deploy uses identical keys.
  await writeFile(path, JSON.stringify(state, null, 2) + "\n", {
    flag: "wx",
    mode: 0o600,
  });
  return state;
}
export async function schemaSources() {
  const root = new URL("../db/", import.meta.url);
  return Promise.all(
    (await readdir(root))
      .filter((f) => /^\d{3}-?.*\.sql$/.test(f))
      .sort()
      .map(async (name) => {
        const sql = await readFile(new URL(name, root), "utf8");
        return {
          name,
          hash: createHash("sha256").update(sql).digest("hex"),
          sql: sql.replace(/^\s*(BEGIN|COMMIT);\s*$/gm, ""),
        };
      }),
  );
}
/** @param {any} client @param {any} state @param {{name:string,hash:string,sql:string}[]} [sources] */
export async function provisionDatabase(client, state, sources = undefined) {
  sources ||= await schemaSources();
  await client.query("BEGIN");
  try {
    await client.query("SELECT pg_advisory_xact_lock(86740231)");
    const existing = await client.query(
      "SELECT to_regclass('app_os.central_ai_installation') AS installation, EXISTS(SELECT 1 FROM pg_namespace WHERE nspname='app_os') AS occupied, EXISTS(SELECT 1 FROM pg_roles WHERE rolname='app_os_api') AS role_exists",
    );
    const info = existing.rows[0];
    if (!info.installation && (info.occupied || info.role_exists))
      throw new Error(
        "Existing App OS storage was found without this installer’s binding. Use a fresh Supabase database; no data was overwritten.",
      );
    await client.query("CREATE SCHEMA IF NOT EXISTS app_os");
    await client.query("SET LOCAL search_path=app_os,public");
    await client.query(
      "CREATE TABLE IF NOT EXISTS central_ai_installation (id boolean PRIMARY KEY DEFAULT true CHECK(id),instance_id uuid NOT NULL); CREATE TABLE IF NOT EXISTS central_ai_schema (name text PRIMARY KEY,sha256 text NOT NULL,applied_at timestamptz NOT NULL DEFAULT now())",
    );
    const bound = await client.query(
      "SELECT instance_id FROM central_ai_installation WHERE id FOR UPDATE",
    );
    if (bound.rows.length && bound.rows[0].instance_id !== state.id)
      throw new Error(
        "This database belongs to another installation. Restore its original volumes; no keys or data were changed.",
      );
    if (!bound.rows.length)
      await client.query(
        "INSERT INTO central_ai_installation(id,instance_id) VALUES(true,$1)",
        [state.id],
      );
    const ledger = await client.query(
      "SELECT name,sha256 FROM central_ai_schema",
    );
    const applied = new Map(ledger.rows.map((row) => [row.name, row.sha256]));
    if (
      [...applied.keys()].some((name) => !sources.some((s) => s.name === name))
    )
      throw new Error(
        "Database schema is newer than this source release. Deploy a compatible release.",
      );
    for (const source of sources) {
      if (applied.has(source.name)) {
        if (applied.get(source.name) !== source.hash)
          throw new Error(
            "An applied schema file changed. Add a new numbered schema update; existing data was preserved.",
          );
      } else {
        await client.query(source.sql);
        await client.query(
          "INSERT INTO central_ai_schema(name,sha256) VALUES($1,$2)",
          [source.name, source.hash],
        );
      }
    }
    await client.query(appRoleSQL);
    // Generated hex only; no operator text is interpolated into SQL.
    if (!/^[0-9a-f]{64}$/.test(state.keys.database))
      throw new Error("Invalid generated database key.");
    await client.query(
      "ALTER ROLE app_os_api LOGIN PASSWORD '" + state.keys.database + "'",
    );
    await client.query(
      "REVOKE ALL ON central_ai_installation,central_ai_schema FROM app_os_api",
    );
    await client.query("COMMIT");
    await client.query("NOTIFY pgrst,'reload schema'");
  } catch (e) {
    await client.query("ROLLBACK");
    throw e;
  }
}
function nativeProfile(agent, model, provider = 'openrouter') {
  return {
    model: {
      provider,
      default: model,
      ...(provider === 'openrouter' ? {base_url: 'https://openrouter.ai/api/v1'} : {}),
    },
    reasoning: "low",
    agent: { system_prompt: agent.instructions, reasoning_effort: "low" },
    timezone: "Australia/Brisbane",
    terminal: { backend: "local" },
    platform_toolsets: {
      cli: ["browser", "web", "skills", "memory", "todo", "michael_os", "laya"],
      whatsapp: ["browser", "web", "skills", "memory", "todo", "michael_os", "laya"],
    },
    browser: { backend: "off" },
    memory: { provider: "hindsight" },
    tts: {
      provider: "piper",
      piper: {
        voice: "/opt/voice-models/en_GB-alan-medium.onnx",
        use_cuda: false,
      },
    },
    stt: { provider: "local", language: "en", local: { model: "base.en" } },
    plugins: { enabled: ["hindsight"], disabled: [] },
    mcp_servers: {
      laya: {
        command: "/opt/hermes/.venv/bin/python",
        args: ["/opt/os-adapter/scripts/hermes-laya-mcp.py"],
      },
    },
  };
}
export async function provisionFiles(root, settings, state) {
  const owners = {
    "app-config": 1000,
    "worker-config": 1000,
    "hermes-config": 10000,
    "phone-config": 10000,
    "memory-config": 1000,
    "laya-config": 10001,
    "os-profiles": 10000,
    "phone-profiles": 10000,
    "hermes-state": 10000,
    "phone-state": 10000,
    "hindsight-state": 1000,
    "hindsight-cache": 1000,
    "laya-cache": 10001,
  };
  for (const [name, uid] of Object.entries(owners))
    await directory(join(root, name), uid);
  const manifest = { version: 1, agents: state.agents };
  const database = new URL(settings.SUPABASE_BOOTSTRAP_DATABASE_URL);
  // Supabase session poolers route custom logins with the project suffix.
  const poolSuffix = database.username.startsWith('postgres.') ? database.username.slice('postgres'.length) : '';
  database.username = 'app_os_api' + poolSuffix;
  database.password = state.keys.database;
  const app = {
    APP_ORIGIN: settings.APP_ORIGIN,
    APP_GATEWAY_SECRET: state.keys.gateway,
    NEXT_PUBLIC_SUPABASE_URL: settings.NEXT_PUBLIC_SUPABASE_URL,
    NEXT_PUBLIC_SUPABASE_ANON_KEY: settings.NEXT_PUBLIC_SUPABASE_ANON_KEY,
    SUPABASE_SERVICE_ROLE_KEY: settings.SUPABASE_SERVICE_ROLE_KEY,
    DATABASE_URL: database.href,
    DATABASE_SCHEMA: "app_os",
    HERMES_BASE_URL: "http://hermes:8642",
    HERMES_API_KEY: state.keys.hermes,
    PREMADE_AGENT_MANIFEST_FILE: "/run/secrets/premade_agents",
  };
  loadRuntimeEnvironment(
    { APP_RUNTIME: "data-api", APP_RUNTIME_CONFIG_FILE: "generated" },
    () => JSON.stringify(app),
  );
  await atomic(join(root, "app-config", "app_api_config"), app, 1000);
  await atomic(join(root, "app-config", "premade_agents"), manifest, 1000);
  await atomic(
    join(root, "app-config", "setup_access_token"),
    settings.SERVICE_PASSWORD_SETUP,
    1000,
  );
  const worker = { ...app };
  delete worker.SUPABASE_SERVICE_ROLE_KEY;
  delete worker.PREMADE_AGENT_MANIFEST_FILE;
  await atomic(join(root, "worker-config", "app_worker_config"), worker, 1000);
  const integrations = {
    OPENROUTER_API_KEY: settings.OPENROUTER_API_KEY,
    OPENAI_API_KEY: settings.OPENAI_API_KEY || '',
    ANTHROPIC_API_KEY: settings.ANTHROPIC_API_KEY || '',
    GEMINI_API_KEY: settings.GEMINI_API_KEY || '',
    FISH_API_KEY: settings.FISH_API_KEY,
    LAYA_API_KEY: state.keys.laya,
    HINDSIGHT_API_KEY: state.keys.hindsight,
    LAYA_URL: "http://laya:8000",
    HINDSIGHT_API_URL: "http://hindsight:8888",
  };
  const phoneProfiles = [...new Set([...settings.whatsapp.contacts.flatMap(c => [c.profile, c.text_profile]), settings.whatsapp.group_profile,
    "leo",
    "leo-whatsapp-text",
    "team-whatsapp-michael-business",
    "team-whatsapp-michael-text",
    "team-whatsapp-social",
  ])];
  for (const volume of ["hermes-config", "phone-config"])
    await atomic(join(root, volume, "whatsapp_routing"), settings.whatsapp, 10000);
  await atomic(
    join(root, "hermes-config", "central_ai_config"),
    { version: 1, integrations, phone_profiles: phoneProfiles },
    10000,
  );
  await atomic(join(root, "hermes-config", "premade_agents"), manifest, 10000);
  const bridge = {
    HERMES_API_KEY: state.keys.hermes,
    HERMES_PROFILE_ROOT: "/opt/data",
    HERMES_PROFILE: "leo",
    HERMES_REPO: "/opt/hermes",
    HERMES_PYTHON: "/opt/hermes/.venv/bin/python",
    HERMES_PREMADE_MANIFEST_FILE: "/run/secrets/premade_agents",
    HERMES_VOICE_MODEL: settings.voiceModel,
    HERMES_VOICE_PROVIDER: "openrouter",
  };
  await atomic(
    join(root, "hermes-config", "hermes_bridge_config"),
    Object.entries(bridge)
      .map(([k, v]) => k + "=" + v)
      .join("\n") + "\n",
    10000,
  );
  for (const name of ["hermes-config", "phone-config"])
    await atomic(
      join(root, name, "whatsapp_setup_key"),
      state.keys.pairing,
      10000,
    );
  for (const [file, value] of Object.entries({
    openrouter_api_key: settings.OPENROUTER_API_KEY,
    hindsight_api_key: state.keys.hindsight,
    hindsight_ui_key: state.keys.hindsightUI,
  }))
    await atomic(join(root, "memory-config", file), value, 1000);
  await atomic(
    join(root, "laya-config", "laya_api_key"),
    state.keys.laya,
    10001,
  );
  const voice = {
    CENTRAL_AI_CALL_SPEECH: settings.speechProvider,
    FISH_VOICE_ID: settings.fishVoiceId,
    FISH_VOICE_NAME: "Jarvis",
    FISH_TTS_MODEL: "s2.1-pro-free",
    FISH_OS_CALL_ENGINE: "stream",
    FISH_ASR_ENABLED: "false",
    FISH_LLM_MODEL: settings.voiceModel,
    FISH_AGENT_BRIDGE_SECRET: state.keys.fishBridge,
  };
  for (const [volume, profiles] of [
    ["os-profiles", state.agents.map((a) => a.native_profile)],
    ["phone-profiles", phoneProfiles],
  ]) {
    await directory(join(root, volume, "profiles"), 10000);
    for (const name of profiles) {
      const agent = state.agents.find((a) => a.native_profile === name) || {
        ...state.agents[0],
        instructions:
          state.agents[0].instructions +
          " This WhatsApp channel has separate history and memory; do not access another person’s private context.",
      };
      const business = name.startsWith("team-whatsapp-michael") || name.startsWith("team-phone-");
      if (business)
        agent.instructions =
          "You are a private assistant on Central AI for this WhatsApp contact. This number has its own conversations, memory and task list. Other contacts’ data and account connections are inaccessible. Use information supplied here. External account access requires a separately authorized connection. Never invent completed actions.";
      if (name === "team-whatsapp-social" || name === settings.whatsapp.group_profile)
        agent.instructions =
          "You are Leo, the team’s assistant on Central AI, in an explicitly configured WhatsApp group. Help draft and discuss team work using information shared here. Do not disclose personal conversations, private memory or another person’s connections. External publishing and actions require their actual approval flow. Use clear English.";
      const home = join(root, volume, "profiles", name);
      await directory(home, 10000);
      // Never rewrite an existing persona, native model preference or memory binding.
      const bank =
        volume === "os-profiles"
          ? name === "leo"
            ? "michael-os-leo"
            : "central-ai-sarah"
          : "whatsapp-" + name;
      const config = nativeProfile(agent, settings.chatModel, settings.chatProvider);
      if (business || name === "team-whatsapp-social") {
        config.phone_business_context = true;
        config.platform_toolsets = {
          cli: ["memory", "todo"],
          whatsapp: ["memory", "todo"],
        };
        config.mcp_servers = {};
      }
      for (const [filename, content] of [
        ["config.yaml", YAML.stringify(config)],
        ["SOUL.md", agent.instructions + "\n"],
      ]) {
        try {
          const file = await lstat(join(home, filename));
          if (!file.isFile() || file.isSymbolicLink())
            throw new Error("Unsafe native profile file.");
        } catch (e) {
          if (e.code !== "ENOENT") throw e;
          await atomic(join(home, filename), content, 10000);
        }
      }
      await directory(join(home, "hindsight"), 10000);
      try {
        const file = await lstat(join(home, "hindsight", "config.json"));
        if (!file.isFile() || file.isSymbolicLink())
          throw new Error("Unsafe native memory file.");
      } catch (e) {
        if (e.code !== "ENOENT") throw e;
        await atomic(
          join(home, "hindsight", "config.json"),
          {
            mode: "local_external",
            api_url: "http://hindsight:8888",
            api_key: state.keys.hindsight,
            bank_id: bank,
            memory_mode: "hybrid",
            auto_retain: true,
            retain_async: true,
            retain_indicator: false,
            auto_recall: true,
            recall_sync: false,
            recall_indicator: false,
            recall_prefetch_method: "recall",
            recall_budget: "low",
            recall_max_tokens: 1200,
            timeout: 15,
          },
          10000,
        );
      }
      // Generated SDK compatibility credentials have one authority: Coolify inputs.
      await profileEnvironment(join(home, ".env"), {
        ...Object.fromEntries(Object.entries(integrations).filter(([key,value]) => value || !['OPENAI_API_KEY','ANTHROPIC_API_KEY','GEMINI_API_KEY'].includes(key))),
        ...voice,
        CENTRAL_AI_VOICE_CONFIG: `/opt/data/profiles/${name}/config.yaml`,
      });
      if (name === "team-whatsapp-michael-business")
        await atomic(
          join(home, "phone-channel.json"),
          { number: "61423947456", purpose: "private-whatsapp-business" },
          10000,
        );
    }
  }
  const keyfile = join(root, "phone-state", "voice-key");
  try {
    const file = await lstat(keyfile);
    if (!file.isFile() || file.isSymbolicLink())
      throw new Error("Unsafe phone transport key file.");
    if ((await readFile(keyfile, "utf8")).trim() !== state.keys.voice)
      throw new Error("Phone transport key does not match its installation.");
  } catch (e) {
    if (e.code !== "ENOENT") throw e;
    await atomic(keyfile, state.keys.voice, 10000);
  }
}
export async function bootstrap(
  env = process.env,
  root = "/bootstrap",
  createClient,
) {
  const settings = installationSettings(env);
  const state = await installationState(root);
  if (!createClient) {
    const { Client } = await import("pg");
    createClient = () =>
      new Client({
        connectionString: settings.SUPABASE_BOOTSTRAP_DATABASE_URL,
        connectionTimeoutMillis: 10000,
      });
  }
  const client = createClient();
  try {
    await client.connect();
    await provisionDatabase(client, state);
    await provisionFiles(root, settings, state);
  } finally {
    await client.end();
  }
  return state.id;
}
if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  try {
    await bootstrap();
    console.log(
      "Central AI installation prepared. Open your domain to create the owner; use SERVICE_PASSWORD_SETUP from Coolify.",
    );
  } catch (e) {
    const safe =
      e.message?.startsWith("Set ") ||
      /^(Existing App OS|This database|An applied schema|Database schema|Installation state|SERVICE_PASSWORD|SUPABASE_BOOTSTRAP|Phone transport|Use a provider|Installation storage)/.test(
        e.message || "",
      );
    console.error(
      safe
        ? e.message
        : "Central AI installation failed. Check the private Supabase connection, permissions and required Coolify settings. Credentials were not logged.",
    );
    process.exitCode = 1;
  }
}
