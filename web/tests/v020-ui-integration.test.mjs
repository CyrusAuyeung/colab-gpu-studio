import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const root = join(process.cwd(), 'src');
const read = (name) => readFileSync(join(root, name), 'utf8');

test('App owns one runtime choice and polls account/instance resources', () => {
  const source = read('App.tsx');
  assert.match(source, /runtimeChoice/);
  assert.match(source, /getGpuAccounts|getGpuInstances|apiFetch/);
  assert.match(source, /execution_policy/);
  assert.match(source, /retain_idle_minutes/);
  assert.doesNotMatch(source, /shutdown_after_job/);
  assert.doesNotMatch(source, /idle_grace_minutes/);
});

test('create page delegates lifecycle controls to RuntimeSelection', () => {
  const create = read('pages/CreatePage.tsx');
  const selection = read('components/RuntimeSelection.tsx');
  assert.match(create, /<RuntimeSelection\b/);
  assert.match(create, /value=\{runtimeChoice\}/);
  assert.match(create, /onChange=\{onRuntimeChoiceChange\}/);
  assert.match(selection, /RuntimeLifecycle = 'save' \| 'batch' \| 'smart' \| 'retain'/);
  assert.match(selection, /id="runtime-lifecycle"/);
  assert.match(selection, /id="retain-idle-minutes"/);
  assert.match(selection, /retainIdleMinutes/);
  assert.doesNotMatch(create, /id="batch-mode-select"/);
});

test('settings uses account directory and compute uses instance center', () => {
  const settings = read('pages/SettingsPage.tsx');
  const compute = read('pages/ComputePage.tsx');
  assert.match(settings, /AccountDirectory/);
  assert.match(compute, /InstanceCenter/);
  assert.doesNotMatch(compute, /GoogleOAuthSetup/);
});

test('App wires retention success back into the instance state without duplicates', () => {
  const source = read('App.tsx');
  assert.match(source, /const updatedInstance = await apiFetch<GpuInstance>\(/);
  assert.match(source, /method: 'PATCH'/);
  assert.match(source, /\/retention/);
  assert.match(source, /setInstances\(\(current\) =>/);
  assert.match(source, /findIndex\(\(item\) => item\.instance_id === updatedInstance\.instance_id\)/);
});

test('jobs render machine history and only expose retention action through callback', () => {
  const source = read('pages/JobsPage.tsx');
  assert.match(source, /instance_id/);
  assert.match(source, /account_alias_snapshot/);
  assert.match(source, /onRetain\?:/);
  assert.match(source, /onInstance\?:/);
  assert.match(source, /key=\{jobKey\}/);
});
