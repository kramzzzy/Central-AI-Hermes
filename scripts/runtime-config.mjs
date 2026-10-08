import { readFileSync } from "node:fs";

const frontendKeys = [
  "APP_RUNTIME",
  "APP_ORIGIN",
  "NEXT_PUBLIC_SUPABASE_URL",
  "NEXT_PUBLIC_SUPABASE_ANON_KEY",
];
const backendKeys = [
  "APP_ORIGIN",
  "APP_GATEWAY_SECRET",
  "NEXT_PUBLIC_SUPABASE_URL",
  "NEXT_PUBLIC_SUPABASE_ANON_KEY",
  "SUPABASE_SERVICE_ROLE_KEY",
  "DATABASE_URL",
  "DATABASE_SCHEMA",
  "DATABASE_POOL_MAX",
  "HERMES_BASE_URL",
  "HERMES_API_KEY",
  "PREMADE_AGENT_MANIFEST_FILE",
];
const providerKeys = [
  "OPENAI_API_KEY",
  "OPENROUTER_API_KEY",
  "FISH_API_KEY",
  "FISH_AUDIO_API_KEY",
  "GEMINI_API_KEY",
  "GOOGLE_API_KEY",
  "GOOGLE_CLIENT_SECRET",
  "GOOGLE_CLIENT_ID",
  "HINDSIGHT_API_KEY",
  "LAYA_API_KEY",
  "RESEND_API_KEY",
];
const privateField = (key) =>
  /^SERVICE_(PASSWORD|SECRET|TOKEN|KEY)_/.test(key) ||
  key === 'SUPABASE_BOOTSTRAP_DATABASE_URL' ||
  /^(DATABASE_|DB_|PG|HERMES_|BETTER_AUTH_|OWNER_|SETUP_|PREMADE_|GOOGLE_CLIENT_|FISH_|HINDSIGHT_|LAYA_)/.test(
    key,
  ) ||
  /(_API_KEY|_SECRET|_PASSWORD|_TOKEN)$/.test(key) ||
  key === "SUPABASE_SERVICE_ROLE_KEY";

function origin(value, field, allowInternal = false) {
  try {
    const url = new URL(value);
    if (
      url.username ||
      url.password ||
      url.search ||
      url.hash ||
      url.pathname !== "/" ||
      !["http:", "https:"].includes(url.protocol)
    )
      throw new Error();
    if (
      !allowInternal &&
      url.protocol !== "https:" &&
      !["localhost", "127.0.0.1"].includes(url.hostname)
    )
      throw new Error();
    return url.origin;
  } catch {
    throw new Error(
      field +
        " must be a valid " +
        (allowInternal ? "private HTTP" : "HTTPS") +
        " origin.",
    );
  }
}
function publicSettings(environment) {
  origin(environment.APP_ORIGIN, "APP_ORIGIN");
  origin(environment.NEXT_PUBLIC_SUPABASE_URL, "NEXT_PUBLIC_SUPABASE_URL");
  const key = environment.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (key?.startsWith("sb_publishable_")) return;
  try {
    if (
      key.split(".").length !== 3 ||
      JSON.parse(Buffer.from(key.split(".")[1], "base64url").toString())
        .role !== "anon"
    )
      throw new Error();
  } catch {
    throw new Error(
      "NEXT_PUBLIC_SUPABASE_ANON_KEY must be a public Supabase key, never an administrator key.",
    );
  }
}

export function validateFrontendEnvironment(environment) {
  const forbidden = [
    ...backendKeys.filter((k) => !frontendKeys.includes(k)),
    ...providerKeys,
    "APP_RUNTIME_CONFIG_FILE",
    "AUTH_PROVIDER",
    "DATABASE_MIGRATION_URL",
    "BETTER_AUTH_SECRET",
    "BETTER_AUTH_URL",
    "OWNER_PASSWORD",
  ];
  const leaked = [
    ...new Set([
      ...forbidden,
      ...Object.keys(environment).filter(
        (key) => privateField(key) && !frontendKeys.includes(key),
      ),
    ]),
  ].filter((key) => Boolean(environment[key]));
  if (leaked.length)
    throw new Error(
      "Remove backend-only fields from the App OS frontend: " +
        leaked.join(", "),
    );
  for (const key of frontendKeys)
    if (!environment[key]) throw new Error("Missing frontend setting: " + key);
  if (environment.APP_RUNTIME !== "frontend")
    throw new Error("The App OS container must use the frontend runtime.");
  publicSettings(environment);
  // Compose supplies this fixed internal service address. Local fixture runs
  // may use another private origin; it is never a browser-controlled URL.
  if (environment.APP_API_ORIGIN)
    origin(environment.APP_API_ORIGIN, "APP_API_ORIGIN", true);
}

/** @param {any} environment @param {(path:string,encoding:string)=>string} [read] @param {boolean} [worker] */
export function loadRuntimeEnvironment(
  environment,
  read = readFileSync,
  worker = false,
) {
  const result = { ...environment };
  if (result.APP_RUNTIME === "frontend") {
    validateFrontendEnvironment(result);
    return result;
  }
  if (result.APP_RUNTIME !== "data-api") return result;
  if (!result.APP_RUNTIME_CONFIG_FILE)
    throw new Error("App OS API requires its private configuration mount.");
  let source;
  try {
    source = JSON.parse(read(result.APP_RUNTIME_CONFIG_FILE, "utf8"));
  } catch {
    throw new Error("The private App OS API configuration could not be read.");
  }
  if (!source || typeof source !== "object" || Array.isArray(source))
    throw new Error("Invalid private App API configuration.");
  const unexpected = Object.keys(source).filter(
    (key) => !backendKeys.includes(key),
  );
  if (unexpected.length)
    throw new Error(
      "Unexpected private App API fields: " + unexpected.join(", "),
    );
  for (const key of [
    "APP_ORIGIN",
    "APP_GATEWAY_SECRET",
    "NEXT_PUBLIC_SUPABASE_URL",
    "NEXT_PUBLIC_SUPABASE_ANON_KEY",
    "DATABASE_URL",
    "DATABASE_SCHEMA",
    "HERMES_BASE_URL",
    "HERMES_API_KEY",
    ...(!worker
      ? ["SUPABASE_SERVICE_ROLE_KEY", "PREMADE_AGENT_MANIFEST_FILE"]
      : []),
  ]) {
    if (key === "HERMES_BASE_URL" && source[key]) {
      source[key] = source[key].trim().replace(/\/$/, "").replace(/:8643$/, ":8642");
      if (
        process.env.DOCKER_CONTAINER === "1" ||
        process.env.NODE_ENV === "production" ||
        process.env.COOLIFY_CONTAINER_NAME ||
        process.env.COOLIFY_URL
      ) {
        if (source[key].includes("localhost") || source[key].includes("127.0.0.1")) {
          source[key] = source[key].replace(/localhost|127\.0\.0\.1/, "hermes");
        }
      }
    }
    if (typeof source[key] !== "string" || !source[key])
      throw new Error("Missing private App API field: " + key);
  }
  for (const [key, value] of Object.entries(source)) {
    if (
      typeof value !== "string" ||
      value.includes("\0") ||
      value.includes("\n") ||
      value.includes("\r")
    )
      throw new Error("Invalid private App API field: " + key);
    if (
      !worker ||
      !["SUPABASE_SERVICE_ROLE_KEY", "PREMADE_AGENT_MANIFEST_FILE"].includes(
        key,
      )
    )
      result[key] = value;
  }
  publicSettings(source);
  origin(source.HERMES_BASE_URL, "HERMES_BASE_URL", true);
  if (
    source.APP_GATEWAY_SECRET.length < 32 ||
    source.HERMES_API_KEY.length < 32
  )
    throw new Error(
      "Use generated private App API/Hermes authentication keys of at least 32 characters.",
    );
  if (source.DATABASE_SCHEMA !== "app_os")
    throw new Error("The fresh App OS API must use the private app_os schema.");
  try {
    const url = new URL(source.DATABASE_URL);
    if (
      !["postgres:", "postgresql:"].includes(url.protocol) ||
      !/^app_os_api(?:\.[a-z0-9]+)?$/.test(url.username)
    )
      throw new Error();
  } catch {
    throw new Error(
      "Use the restricted app_os_api database role for the App OS API.",
    );
  }
  const inheritedPrivate = Object.keys(environment).filter(
    (key) =>
      privateField(key) &&
      !["APP_RUNTIME_CONFIG_FILE", "SETUP_TOKEN_FILE"].includes(key),
  );
  if (inheritedPrivate.some((key) => environment[key]))
    throw new Error(
      "Private App API settings must come from its single configuration mount.",
    );
  if (worker) {
    delete result.SUPABASE_SERVICE_ROLE_KEY;
    delete result.PREMADE_AGENT_MANIFEST_FILE;
  }
  for (const key of providerKeys)
    if (result[key])
      throw new Error("Provider credentials belong in Hermes: " + key);
  result.BETTER_AUTH_URL = source.APP_ORIGIN;
  result.BETTER_AUTH_SECRET = source.APP_GATEWAY_SECRET;
  result.AUTH_PROVIDER = "supabase";
  return result;
}
