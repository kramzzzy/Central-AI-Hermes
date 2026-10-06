import { validateFrontendEnvironment } from './runtime-config.mjs';

export const providers = {
  openrouter: { label: 'OpenRouter', key: 'OPENROUTER_API_KEY', model: 'openai/gpt-4.1-mini', url: 'https://openrouter.ai/api/v1/key' },
  openai: { label: 'OpenAI API', key: 'OPENAI_API_KEY', model: 'gpt-4.1-mini', url: 'https://api.openai.com/v1/models' },
  anthropic: { label: 'Anthropic API', key: 'ANTHROPIC_API_KEY', model: 'claude-sonnet-4-6', url: 'https://api.anthropic.com/v1/models' },
  gemini: { label: 'Google Gemini API', key: 'GEMINI_API_KEY', model: 'gemini-2.5-flash', url: 'https://generativelanguage.googleapis.com/v1beta/models' },
  'openai-codex': { label: 'ChatGPT / Codex subscription', model: 'gpt-5.6-luna' },
};
export class SetupError extends Error {}
export function field(value, label, { optional = false, max = 8192 } = {}) {
  if (typeof value !== 'string' || value.length > max || /[\r\n\0]/.test(value) || (!optional && !value.trim()))
    throw new SetupError('Enter a valid ' + label + '.');
  return value.trim();
}
export function httpsOrigin(value, label) {
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw Error();
    return url.origin;
  } catch { throw new SetupError('Enter the HTTPS ' + label + ' without a path.'); }
}
export function databaseSettings(input, appOrigin) {
  if (!['cloud', 'server'].includes(input.kind)) throw new SetupError('Choose where Supabase is hosted.');
  const origin = httpsOrigin(input.url, 'Supabase project URL');
  const publicKey = field(input.publicKey, 'public Supabase key');
  try { validateFrontendEnvironment({ APP_RUNTIME: 'frontend', APP_ORIGIN: appOrigin, NEXT_PUBLIC_SUPABASE_URL: origin, NEXT_PUBLIC_SUPABASE_ANON_KEY: publicKey }); }
  catch { throw new SetupError('Use the public anon or publishable key in the public key field.'); }
  const adminKey = field(input.adminKey, 'private Supabase key');
  if (!adminKey.startsWith('sb_secret_')) {
    try { if (JSON.parse(Buffer.from(adminKey.split('.')[1], 'base64url')).role !== 'service_role') throw Error(); }
    catch { throw new SetupError('Use the private service-role or secret key in the server key field.'); }
  }
  const host = field(input.host, 'database host', { max: 253 });
  if (!/^[a-zA-Z0-9.-]+$/.test(host) || host.includes('..')) throw new SetupError('Enter a database hostname, without https:// or a port.');
  const username = field(input.username || 'postgres', 'database username', { max: 100 });
  if (!/^postgres(?:\.[a-z0-9]+)?$/.test(username)) throw new SetupError('Use the postgres administrator username from Supabase Connect.');
  const port = String(input.port || '5432');
  if (port !== '5432') throw new SetupError('Use a direct connection or session pooler on port 5432.');
  const name = field(input.database || 'postgres', 'database name', { max: 63 });
  if (!/^[a-zA-Z0-9_-]+$/.test(name)) throw new SetupError('Enter a valid database name.');
  const password = field(input.password, 'database password');
  if (input.kind === 'cloud') {
    const ref = new URL(origin).hostname.match(/^([a-z0-9]+)\.supabase\.co$/)?.[1];
    if (!ref) throw new SetupError('For Supabase Cloud, use the original project-ref.supabase.co URL from Project Settings.');
    const direct = host === 'db.' + ref + '.supabase.co' && username === 'postgres';
    const pooled = /^[a-z0-9.-]+\.pooler\.supabase\.com$/.test(host) && username === 'postgres.' + ref;
    if (!direct && !pooled) throw new SetupError('The database host and username must belong to this Supabase project. Copy them from its Connect panel.');
  }
  const connection = new URL('postgresql://localhost');
  connection.hostname = host; connection.port = port; connection.username = username;
  connection.password = encodeURIComponent(input.password); connection.pathname = '/' + name;
  if (input.kind === 'cloud' || !input.network) connection.searchParams.set('sslmode', 'verify-full');
  let network = '';
  if (input.kind === 'server') {
    network = field(input.network || '', 'Docker network', { optional: true, max: 100 });
    if (network && !/^[a-zA-Z0-9][a-zA-Z0-9_.-]*$/.test(network)) throw new SetupError('Enter a valid Docker network name.');
  }
  return {
    NEXT_PUBLIC_SUPABASE_URL: origin, NEXT_PUBLIC_SUPABASE_ANON_KEY: publicKey,
    SUPABASE_SERVICE_ROLE_KEY: adminKey, SUPABASE_BOOTSTRAP_DATABASE_URL: connection.href,
    SUPABASE_DATABASE_NETWORK: network, SUPABASE_DATABASE_EXTERNAL: network ? 'true' : 'false',
  };
}
export function modelSettings(input, signedIn = false) {
  const provider = Object.hasOwn(providers, input.provider) ? providers[input.provider] : null;
  if (!provider) throw new SetupError('Choose a supported model provider.');
  const model = field(input.model, 'model ID', { max: 160 });
  if (!/^[a-zA-Z0-9_./:-]+$/.test(model)) throw new SetupError('Enter a provider model ID.');
  if (input.provider === 'openai-codex' && !signedIn) throw new SetupError('Complete ChatGPT sign-in first.');
  const env = { HERMES_CHAT_PROVIDER: input.provider, HERMES_CHAT_MODEL: model };
  if (provider.key) env[provider.key] = field(input.apiKey, 'provider API key');
  // Streaming calls and Hindsight have their own model requests. A consumer
  // subscription must never be represented as paying for these API services.
  env.OPENROUTER_API_KEY = input.provider === 'openrouter' ? env.OPENROUTER_API_KEY : field(input.openrouterKey, 'OpenRouter key for voice and memory');
  return env;
}

export async function verifyProvider(input, fetcher = fetch) {
  const provider = providers[input.provider];
  if (!provider?.key) return;
  const headers = input.provider === 'anthropic'
    ? { 'x-api-key': input.apiKey, 'anthropic-version': '2023-06-01' }
    : input.provider === 'gemini' ? { 'x-goog-api-key': input.apiKey } : { Authorization: 'Bearer ' + input.apiKey };
  const response = await fetcher(provider.url, { headers, redirect: 'error', signal: AbortSignal.timeout(15000) });
  if (!response.ok) throw new SetupError('The provider did not accept this key. Check API access and try again.');
  // Key/catalog checks do not perform inference or certify available credit.
  return { verified: true };
}
