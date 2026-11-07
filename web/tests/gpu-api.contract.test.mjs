import assert from 'node:assert/strict';
import { test, beforeEach } from 'node:test';
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { tmpdir } from 'node:os';
import { transform } from 'esbuild';

const sourcePath = join(process.cwd(), 'src/lib/gpu-api.ts');
let api;
let requests;

beforeEach(async () => {
  requests = [];
  globalThis.window = {
    setTimeout,
    clearTimeout,
    dispatchEvent() {},
  };
  globalThis.sessionStorage = {
    getItem() { return ''; },
    setItem() {},
    removeItem() {},
  };
  globalThis.fetch = async (url, init) => {
    requests.push({ url: String(url), init });
    return new Response(JSON.stringify({ data: { ok: true }, request_id: 'contract-1' }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  };
  const source = readFileSync(sourcePath, 'utf8');
  const compiled = await transform(source, {
    loader: 'ts',
    format: 'esm',
    target: 'es2020',
    define: { 'import.meta.env.VITE_API_BASE_URL': '""' },
    sourcemap: false,
  });
  const modulePath = join(tmpdir(), `gpu-api-contract-${Date.now()}-${Math.random()}.mjs`);
  writeFileSync(modulePath, compiled.code);
  api = await import(`${pathToFileURL(modulePath).href}?test=${Math.random()}`);
});

function lastRequest() {
  assert.equal(requests.length, 1);
  return requests[0];
}

function header(request, name) {
  return request.init.headers instanceof Headers
    ? request.init.headers.get(name)
    : request.init.headers?.[name];
}

async function jsonBody(call) {
  await call;
  const request = lastRequest();
  return { request, body: JSON.parse(request.init.body) };
}

test('exports the v0.2 account, instance, and workbench client functions', () => {
  for (const name of [
    'getGoogleAccounts', 'createGoogleAccount', 'updateGoogleAccount',
    'disconnectGoogleAccount', 'startGoogleAccountAuth', 'getAccountRuntimes',
    'listGpuInstances', 'getGpuInstance', 'getInstanceJobs', 'getInstanceEvents',
    'setInstanceRetention', 'stopInstance', 'getWorkbench', 'workbenchAction',
  ]) assert.equal(typeof api[name], 'function', `${name} should be exported`);
});

test('account writes use the account routes and caller-supplied idempotency key', async () => {
  let result = await jsonBody(api.createGoogleAccount({ alias: 'main' }, 'acct-create-1'));
  assert.equal(result.request.url, '/api/gpu/accounts');
  assert.equal(result.request.init.method, 'POST');
  assert.equal(header(result.request, 'Idempotency-Key'), 'acct-create-1');
  assert.deepEqual(result.body, { alias: 'main' });

  requests = [];
  result = await jsonBody(api.updateGoogleAccount('account/1', { enabled: false }, 'acct-update-1'));
  assert.equal(result.request.url, '/api/gpu/accounts/account%2F1');
  assert.equal(result.request.init.method, 'PATCH');
  assert.equal(header(result.request, 'Idempotency-Key'), 'acct-update-1');
  assert.deepEqual(result.body, { enabled: false });

  requests = [];
  result = await jsonBody(api.disconnectGoogleAccount('account/1', 'acct-disconnect-1'));
  assert.equal(result.request.url, '/api/gpu/accounts/account%2F1/disconnect');
  assert.equal(result.request.init.method, 'POST');
  assert.equal(header(result.request, 'Idempotency-Key'), 'acct-disconnect-1');
});

test('account auth and runtime reads preserve the typed route contract', async () => {
  await api.startGoogleAccountAuth('account/1', 'colab', 'oauth-1');
  let request = lastRequest();
  assert.equal(request.url, '/api/gpu/accounts/account%2F1/auth/colab/start');
  assert.equal(header(request, 'Idempotency-Key'), 'oauth-1');
  assert.deepEqual(JSON.parse(request.init.body), { purpose: 'colab' });

  requests = [];
  await api.getAccountRuntimes('account/1');
  request = lastRequest();
  assert.equal(request.url, '/api/gpu/accounts/account%2F1/runtimes');
  assert.equal(request.init.method, undefined);
});

test('instance reads encode ids and query state', async () => {
  await api.listGpuInstances({ state: 'retained_idle', account_id: 'acct/1' });
  let request = lastRequest();
  assert.equal(request.url, '/api/gpu/instances?state=retained_idle&account_id=acct%2F1');

  requests = [];
  await api.getGpuInstance('instance/1');
  assert.equal(lastRequest().url, '/api/gpu/instances/instance%2F1');
  requests = [];
  await api.getInstanceJobs('instance/1');
  assert.equal(lastRequest().url, '/api/gpu/instances/instance%2F1/jobs');
  requests = [];
  await api.getInstanceEvents('instance/1');
  assert.equal(lastRequest().url, '/api/gpu/instances/instance%2F1/events');
});

test('retention, stop, and workbench writes send exact safe bodies', async () => {
  let result = await jsonBody(api.setInstanceRetention('instance/1', {
    retention_mode: 'retain', idle_minutes: 15, expected_version: 7,
  }, 'retain-1'));
  assert.equal(result.request.url, '/api/gpu/instances/instance%2F1/retention');
  assert.equal(result.request.init.method, 'PATCH');
  assert.equal(header(result.request, 'Idempotency-Key'), 'retain-1');
  assert.deepEqual(result.body, { retention_mode: 'retain', idle_minutes: 15, expected_version: 7 });

  requests = [];
  result = await jsonBody(api.stopInstance('instance/1', 'stop-1'));
  assert.equal(result.request.url, '/api/gpu/instances/instance%2F1/stop');
  assert.equal(header(result.request, 'Idempotency-Key'), 'stop-1');
  assert.deepEqual(result.body, { confirmed: true });

  requests = [];
  await api.getWorkbench('instance/1');
  assert.equal(lastRequest().url, '/api/gpu/instances/instance%2F1/workbench');
  requests = [];
  result = await jsonBody(api.workbenchAction('instance/1', 'health', { verbose: true }, 'workbench-1'));
  assert.equal(result.request.url, '/api/gpu/instances/instance%2F1/workbench/actions');
  assert.equal(header(result.request, 'Idempotency-Key'), 'workbench-1');
  assert.deepEqual(result.body, { action: 'health', verbose: true });
  requests = [];
  result = await jsonBody(api.workbenchAction('instance/1', 'get_reviewed_graph', {
    workflow_key: 'demo-mock', workflow_version: 1,
  }, 'graph-1'));
  assert.deepEqual(result.body, { action: 'get_reviewed_graph', workflow_key: 'demo-mock', workflow_version: 1 });
});

test('Google OAuth config reads target the shared app or the selected account', async () => {
  await api.getGoogleClientConfig();
  let request = lastRequest();
  assert.equal(request.url, '/api/gpu/auth/google/config');
  assert.equal(request.init.method, undefined);

  requests = [];
  await api.getGoogleClientConfig('account/1');
  request = lastRequest();
  assert.equal(request.url, '/api/gpu/auth/google/config?account_id=account%2F1');
});

test('Google OAuth config writes include account_id only for account-specific apps', async () => {
  let result = await jsonBody(api.saveGoogleClientConfig({ client_id: 'client', client_secret: 'secret1234', confirmed: true }));
  assert.equal(result.request.url, '/api/gpu/auth/google/config');
  assert.deepEqual(result.body, { client_id: 'client', client_secret: 'secret1234', confirmed: true });

  requests = [];
  result = await jsonBody(api.saveGoogleClientConfig({ account_id: 'account/1', client_json: { web: {} }, confirmed: true }));
  assert.equal(result.request.url, '/api/gpu/auth/google/config');
  assert.deepEqual(result.body, { account_id: 'account/1', client_json: { web: {} }, confirmed: true });
});

test('createGpuBatch accepts the v0.2 retain mode and envelopes are unwrapped', async () => {
  const result = await api.createGpuBatch([], 'retain', 900, 'batch-1');
  assert.deepEqual(result, { ok: true });
  const request = lastRequest();
  assert.equal(header(request, 'Idempotency-Key'), 'batch-1');
  assert.deepEqual(JSON.parse(request.init.body), { jobs: [], mode: 'retain', idle_seconds: 300 });
});
