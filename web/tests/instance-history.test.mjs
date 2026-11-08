import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { transform } from 'esbuild';

test('duration helper formats completed, hour, missing and in-progress values honestly', async () => {
  const source = readFileSync('src/lib/job-history.ts', 'utf8').replace(/^import .*?;\n/m, '') + '\n' + readFileSync('src/lib/job-stage-rows.ts', 'utf8');
  const { code } = await transform(source, { loader: 'ts', format: 'esm' });
  const module = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
  assert.equal(module.formatDuration(0), '0秒');
  assert.equal(module.formatDuration(62), '1分2秒');
  assert.equal(module.formatDuration(3661), '1小时1分1秒');
  assert.equal(module.formatDuration(null), '—');
  assert.equal(module.formatTiming({ seconds: null, status: 'in_progress' }), '进行中');
});

test('workbench contains a separate offline history entry and graph preview component', () => {
  const source = readFileSync('src/components/InstanceWorkbench.tsx', 'utf8');
  assert.match(source, /历史日志/);
  assert.match(source, /getInstanceHistory/);
  assert.match(source, /WorkflowGraphPreview/);
  assert.match(source, /jobStateLabel\(job\.state\)/);
  assert.match(source, /jobStageLabel\(name\)/);
  assert.match(source, /timing_details/);
});
