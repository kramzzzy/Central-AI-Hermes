import http from 'node:http';
import { timingSafeEqual, createHash, randomBytes } from 'node:crypto';
import { readFile, writeFile, mkdir, rename, chown } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve, join } from 'node:path';
import { Client } from 'pg';
import { databaseSettings, modelSettings, verifyProvider, SetupError, field, httpsOrigin } from './first-run-config.mjs';

const same = (a, b) => timingSafeEqual(createHash('sha256').update(a).digest(), createHash('sha256').update(b).digest());
export async function privateJSON(path, value) {
  await mkdir(resolve(path, '..'), { recursive: true, mode: 0o700 });
  const tmp = path + '.' + randomBytes(8).toString('hex');
  await writeFile(tmp, JSON.stringify(value), { mode: 0o600, flag: 'wx' });
  await rename(tmp, path);
}
async function readJSON(path) { try { return JSON.parse(await readFile(path, 'utf8')); } catch (e) { if (e.code === 'ENOENT') return null; throw e; } }

export async function checkDatabase(env, createClient = options => new Client(options), fetcher = fetch) {
  const api = env.NEXT_PUBLIC_SUPABASE_URL;
  for (const [path, key] of [['/auth/v1/settings', env.NEXT_PUBLIC_SUPABASE_ANON_KEY], ['/auth/v1/admin/users?page=1&per_page=1', env.SUPABASE_SERVICE_ROLE_KEY]]) {
    const response = await fetcher(api + path, { redirect: 'error', headers: { apikey: key, Authorization: 'Bearer ' + key }, signal: AbortSignal.timeout(12000) });
    if (!response.ok) throw new SetupError('Supabase did not accept the project URL and keys. Check that all three belong to the same project.');
    await response.body?.cancel();
  }
  const client = createClient({ connectionString: env.SUPABASE_BOOTSTRAP_DATABASE_URL, connectionTimeoutMillis: 10000, query_timeout: 10000 });
  try {
    await client.connect();
    const result = await client.query("SELECT (EXISTS(SELECT 1 FROM pg_namespace WHERE nspname='app_os') OR EXISTS(SELECT 1 FROM pg_roles WHERE rolname='app_os_api')) AS occupied, rolcreaterole FROM pg_roles WHERE rolname=current_user");
    if (!result.rows[0]?.rolcreaterole) throw new SetupError('The database login needs permission to create the restricted application role. Use the administrator connection from Supabase Connect.');
    if (result.rows[0].occupied) throw new SetupError('This database already has an App OS installation. Use its existing app or a separate Supabase project.');
  } finally { await client.end(); }
}

export function createSetupServer({ root = '/setup', origin = process.env.APP_ORIGIN, token = process.env.SERVICE_PASSWORD_SETUP,
  fetcher = fetch, databaseCheck = checkDatabase, hermesConfig = '/hermes-config' } = {}) {
  if (!token || token.length < 32) throw new Error('Set SERVICE_PASSWORD_SETUP before starting setup.');
  httpsOrigin(origin, 'app domain');
  let busy = false;
  const attempts = [];
  const saved = name => join(root, name + '.json');
  async function control(config, method, path, body) {
    const response = await fetcher(config.url + '/api/v1' + path, { method, redirect: 'error',
      headers: { Authorization: 'Bearer ' + config.token, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(25000) });
    if (!response.ok) throw new SetupError('Coolify could not complete this step. Check token permissions and retry.');
    return response.json();
  }
  async function fresh() {
    if (await readJSON(join(root, 'state.json'))) throw new SetupError('This installation is already provisioned. Open the existing app; changing its database requires a separate migration.');
  }
  async function oauth(action) {
    const r = await fetcher('http://setup-auth:3001/' + action, { method: 'POST', headers: { Authorization: 'Bearer ' + token }, signal: AbortSignal.timeout(60000) });
    if (!r.ok) throw new SetupError('Subscription sign-in is unavailable. Retry or choose an API key.');
    return r.json();
  }
  async function act(action, body) {
    const progress = await readJSON(saved('wizard')) || {};
    if (action === 'status') return { phase: progress.phase || 'server', databaseReady: !!progress.databaseReady, modelReady: !!progress.modelReady, origin };
    if (action === 'deployment') {
      if (!progress.deployment) return { state: 'waiting' };
      const config = await readJSON(saved('control'));
      const result = await control(config, 'GET', '/deployments/' + progress.deployment);
      return { state: result.status === 'finished' ? 'ready' : ['failed', 'cancelled', 'canceled'].includes(result.status) ? 'failed' : 'pending' };
    }
    if (action === 'retry') {
      if (!progress.deployment) throw new SetupError('Check the deployment in Coolify before retrying an unconfirmed installation.');
      const config = await readJSON(saved('control'));
      const previous = await control(config, 'GET', '/deployments/' + progress.deployment);
      if (!['failed','cancelled','canceled'].includes(previous.status)) throw new SetupError('Wait for the current deployment to finish.');
      const result = await control(config, 'POST', '/deploy', { uuid: config.application });
      const uuid = result.deployments?.[0]?.deployment_uuid;
      if (!uuid) throw new SetupError('Check the deployment status in Coolify before retrying.');
      await privateJSON(saved('wizard'), { ...progress, phase: 'deploying', deployment: uuid });
      return { state: 'pending' };
    }
    await fresh();
    if (progress.phase === 'deploying') throw new SetupError('Installation is applying. Wait for it to finish.');
    if (action === 'server') {
      const config = { url: httpsOrigin(body.url, 'Coolify URL'), application: field(body.application, 'Coolify application ID', { max: 64 }), token: field(body.token, 'Coolify API token') };
      if (!/^[a-zA-Z0-9]{10,64}$/.test(config.application)) throw new SetupError('Copy the application ID from this resource’s Coolify URL.');
      const app = await control(config, 'GET', '/applications/' + config.application);
      if (app.build_pack !== 'dockercompose' || !['/compose.setup.yaml', 'compose.setup.yaml'].includes(app.docker_compose_location)) throw new SetupError('Choose the new application running compose.setup.yaml. Existing production applications cannot be replaced here.');
      const records = await control(config, 'GET', '/applications/' + config.application + '/envs');
      const env = Object.fromEntries(records.filter(v => !v.is_preview).map(v => [v.key, v.value || '']));
      if (!same(env.SERVICE_PASSWORD_SETUP || '', token) || env.APP_ORIGIN !== origin) throw new SetupError('This Coolify resource does not match this setup page.');
      const previous = await readJSON(saved('control'));
      if (previous && (previous.application !== config.application || previous.url !== config.url)) throw new SetupError('This wizard is already bound to a different resource.');
      await privateJSON(saved('control'), config);
      await privateJSON(saved('backup'), { env: records, compose: app.docker_compose_location });
      await privateJSON(saved('wizard'), { ...progress, phase: 'database' });
      return { ok: true };
    }
    const config = await readJSON(saved('control'));
    if (!config) throw new SetupError('Connect this Coolify resource first.');
    if (action === 'database') {
      const env = databaseSettings({...body, network: body.kind === 'server' ? process.env.SUPABASE_DATABASE_NETWORK || '' : ''}, origin);
      await databaseCheck(env);
      await privateJSON(saved('database'), env);
      await privateJSON(saved('wizard'), { ...progress, phase: 'model', databaseReady: true });
      return { ok: true };
    }
    if (action === 'oauth-start' || action === 'oauth-status') return oauth(action === 'oauth-start' ? 'start' : 'status');
    if (action === 'model') {
      const signed = body.provider === 'openai-codex' && (await oauth('status')).state === 'connected';
      const env = modelSettings(body, signed);
      await verifyProvider(body, fetcher);
      if (body.provider !== 'openrouter') await verifyProvider({ provider: 'openrouter', apiKey: env.OPENROUTER_API_KEY }, fetcher);
      await privateJSON(saved('model'), env);
      await privateJSON(saved('wizard'), { ...progress, phase: 'review', modelReady: true });
      return { ok: true };
    }
    if (action === 'install') {
      const database = await readJSON(saved('database')), model = await readJSON(saved('model'));
      if (!database || !model) throw new SetupError('Complete the database and model steps first.');
      await databaseCheck(database);
      if (model.HERMES_CHAT_PROVIDER === 'openai-codex' && (await oauth('status')).state !== 'connected') throw new SetupError('Complete ChatGPT sign-in first.');
      const environment = { ...database, ...model,
        SUPABASE_DATABASE_NETWORK: database.SUPABASE_DATABASE_NETWORK || config.application + '-database',
        WHATSAPP_OWNER: '', WHATSAPP_BUSINESS_CONTACT: '', WHATSAPP_GROUP: '', WHATSAPP_TEAM_CONTACTS: '[]' };
      await control(config, 'PATCH', '/applications/' + config.application + '/envs/bulk', { data: Object.entries(environment).map(([key, value]) => ({ key, value, is_preview: false, is_buildtime: key.startsWith('NEXT_PUBLIC_'), is_runtime: true, is_literal: true })) });
      const records = await control(config, 'GET', '/applications/' + config.application + '/envs');
      for (const [key, value] of Object.entries(environment)) if (!records.some(row => !row.is_preview && row.key === key && row.value === value)) throw new SetupError('Coolify has not confirmed the saved settings. Retry installation.');
      // Reuse the same fixed resource for later voice and WhatsApp wizard edits.
      await privateJSON(join(hermesConfig, 'coolify_voice'), config);
      if (process.platform === 'linux' && process.getuid() === 0) { await chown(hermesConfig, 10000, 10000); await chown(join(hermesConfig, 'coolify_voice'), 10000, 10000); }
      await control(config, 'PATCH', '/applications/' + config.application, { docker_compose_location: '/compose.yaml' });
      // Record intent before dispatch: an interrupted HTTP response must not
      // turn a second click into a second deployment.
      await privateJSON(saved('wizard'), { ...progress, phase: 'deploying', deployment: null });
      let uuid;
      try {
        const deployment = await control(config, 'POST', '/deploy', { uuid: config.application });
        uuid = deployment.deployments?.[0]?.deployment_uuid;
      } catch { return { state: 'pending', unconfirmed: true }; }
      if (!uuid) return { state: 'pending', unconfirmed: true };
      await privateJSON(saved('wizard'), { ...progress, phase: 'deploying', deployment: uuid });
      return { ok: true, state: 'pending' };
    }
    throw new SetupError('Unknown setup action.');
  }
  return http.createServer(async (req, res) => {
    const headers = { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
      'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'" };
    const send = (status, value) => { res.writeHead(status, { ...headers, 'Content-Type': 'application/json' }); res.end(JSON.stringify(value)); };
    try {
      if (req.method === 'GET' && req.url === '/health') return send(200, { status: 'ok', setup: true });
      const assets = { '/': ['html', 'text/html'], '/setup': ['html', 'text/html'], '/setup.js': ['js', 'text/javascript'], '/setup.css': ['css', 'text/css'] };
      if (req.method === 'GET' && assets[req.url]) {
        const [suffix, type] = assets[req.url];
        const data = await readFile(new URL('./first-run-ui.' + suffix, import.meta.url));
        res.writeHead(200, { ...headers, 'Content-Type': type }); return res.end(data);
      }
      if (req.method !== 'POST' || req.url !== '/setup-api') return send(404, { error: 'Not found' });
      if (req.headers.origin !== origin) return send(403, { error: 'Open setup from the configured app domain.' });
      const now = Date.now(); while (attempts[0] < now - 60000) attempts.shift();
      if (attempts.length >= 20) return send(429, { error: 'Too many attempts. Wait a minute and retry.' });
      if (!same(req.headers.authorization || '', 'Bearer ' + token)) { attempts.push(now); return send(401, { error: 'Enter the private setup token from this application’s Coolify environment.' }); }
      let raw = ''; for await (const chunk of req) { raw += chunk; if (raw.length > 40000) throw new SetupError('Setup request is too large.'); }
      let body; try { body = JSON.parse(raw); } catch { throw new SetupError('Invalid setup request.'); }
      if (!body || typeof body !== 'object' || Array.isArray(body)) throw new SetupError('Invalid setup request.');
      if (busy) return send(409, { error: 'Another setup step is running. Please wait.' });
      busy = true;
      try { send(200, await act(body.action, body)); } finally { busy = false; }
    } catch (e) { send(400, { error: e instanceof SetupError ? e.message : 'Connection failed. Check the supplied settings and server reachability, then retry. No credentials were logged.' }); }
  });
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  createSetupServer().listen(Number(process.env.PORT || 3000), '0.0.0.0');
}
