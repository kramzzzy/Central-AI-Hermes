const $ = id => document.getElementById(id);
let access = '', busy = false, authTimer, deploymentTimer;
function show(step) {
  for (const id of ['unlock', 'server', 'database', 'model', 'review', 'deploying']) $(id).hidden = id !== step;
  document.querySelectorAll('[data-step]').forEach(el => el.classList.toggle('active', el.dataset.step === step));
  $('notice').textContent = '';
  if (step === 'deploying') pollDeployment();
  else { clearTimeout(deploymentTimer); clearTimeout(authTimer); }
}
async function api(action, body = {}) {
  const response = await fetch('/setup-api', { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + access }, body: JSON.stringify({ ...body, action }) });
  const result = await response.json();
  if (!response.ok) throw Error(result.error || 'Setup could not complete this step.');
  return result;
}
async function run(fn) {
  if (busy) return;
  busy = true; $('notice').textContent = '';
  document.querySelectorAll('button').forEach(b => b.disabled = true);
  try { await fn(); } catch (error) { $('notice').textContent = error.message; }
  finally { busy = false; document.querySelectorAll('button').forEach(b => b.disabled = false); }
}
$('unlock').addEventListener('submit', event => { event.preventDefault(); run(async () => {
  access = $('access').value; const status = await api('status'); show(status.phase);
}); });
for (const [name, next] of [['server', 'database'], ['database', 'model'], ['model', 'review']]) {
  $(name).addEventListener('submit', event => { event.preventDefault(); run(async () => {
    const values = Object.fromEntries(new FormData($(name))); await api(name, values);
    $(name).querySelectorAll('input[type=password]').forEach(input => input.value = ''); show(next);
  }); });
}
document.querySelectorAll('[data-back]').forEach(button => button.addEventListener('click', () => show(button.dataset.back)));
$('kind').addEventListener('change', () => {
  $('database-hint').textContent = $('kind').value === 'cloud'
    ? 'Supabase → Connect → Session pooler. Copy its host and username below. Use port 5432.'
    : 'Use the private database hostname on your server. Your host connects the Supabase Docker network to this setup resource once in Coolify.';
});
const models = { openrouter: 'openai/gpt-4.1-mini', openai: 'gpt-4.1-mini', anthropic: 'claude-sonnet-4-6', gemini: 'gemini-2.5-flash', 'openai-codex': 'gpt-5.6-luna' };
function providerChanged() {
  const p = $('provider').value, subscription = p === 'openai-codex';
  $('api-key-label').hidden = subscription; $('subscription').hidden = !subscription;
  $('api-key-label').querySelector('input').required = !subscription;
  $('openrouter-label').hidden = p === 'openrouter'; $('openrouter-label').querySelector('input').required = p !== 'openrouter';
  $('model-id').value = models[p];
  $('provider-help').textContent = p === 'anthropic' ? 'API usage is billed separately from Claude subscriptions. This setup uses the Anthropic API-key connection.'
    : p === 'gemini' ? 'Use a Google AI Studio API key. A Gemini consumer subscription does not connect through this option.'
    : subscription ? 'Uses Hermes’s native ChatGPT/Codex sign-in. This does not provide general OpenAI API credit.' : 'Use the API key from your provider account.';
  clearTimeout(authTimer);
}
$('provider').addEventListener('change', providerChanged); providerChanged();
function authDisplay(value) {
  $('signin-status').textContent = value.state === 'connected' ? 'ChatGPT account connected.' : value.message || 'Waiting for sign-in…';
  $('signin-code').textContent = value.code || '';
  $('signin-link').hidden = value.url !== 'https://auth.openai.com/codex/device';
  if (!$('signin-link').hidden) $('signin-link').href = value.url;
  if (value.state === 'pending') authTimer = setTimeout(async () => { try { authDisplay(await api('oauth-status')); } catch { $('signin-status').textContent = 'Could not check sign-in. Click Sign in to reconnect.'; } }, 5000);
}
$('signin').addEventListener('click', () => run(async () => authDisplay(await api('oauth-start'))));
async function install() { await api('install'); show('deploying'); }
$('install').addEventListener('click', () => run(install));
$('retry').addEventListener('click', () => run(async () => { await api('retry'); $('retry').hidden = true; show('deploying'); }));
async function pollDeployment() {
  try {
    const health = await fetch('/api/health', { cache: 'no-store' });
    if (health.ok && (await health.json()).status === 'ok') { $('deployment-status').textContent = 'Your workspace is ready.'; $('continue').hidden = false; return; }
  } catch {}
  try {
    const result = await api('deployment');
    if (result.state === 'failed') { $('deployment-status').textContent = 'Installation failed. Review the Coolify deployment log and retry.'; $('retry').hidden = false; return; }
  } catch {}
  deploymentTimer = setTimeout(pollDeployment, 10000);
}
