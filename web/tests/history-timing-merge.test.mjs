import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { transform } from 'esbuild';

test('partial timing details retain completed legacy stages and active stages override legacy values', async () => {
  const source = readFileSync('src/components/InstanceWorkbench.tsx', 'utf8');
  const body = source.slice(source.indexOf('function jobTiming('), source.indexOf('\nconst defaultWorkbenchApi'));
  const helpers = readFileSync('src/lib/job-history.ts', 'utf8').replace(/^import .*?;\n/m, '') + '\n' + readFileSync('src/lib/job-stage-rows.ts', 'utf8');
  const { code } = await transform(helpers + '\n' + body + '\nexport { jobTiming };', { loader: 'ts', format: 'esm' });
  const { jobTiming } = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
  assert.deepEqual(jobTiming({timings: {restore_env: 40, sampling: 8}, timing_details: {sampling: {seconds: null, status: 'in_progress'}}}), [
    ['restore_env', '恢复运行环境', '40秒'],
    ['sampling', '采样生成', '进行中'],
  ]);
});

test('timing rows expose source and preserve an honest in-progress value', async () => {
  const source = readFileSync('src/lib/job-history.ts', 'utf8').replace(/^import .*?;\n/m, '') + '\n' + readFileSync('src/lib/job-stage-rows.ts', 'utf8');
  const { code } = await transform(source, { loader: 'ts', format: 'esm' });
  const { timingRows } = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
  assert.deepEqual(timingRows({
    timings: { restore_env: 40 },
    timing_details: {
      restore_env: { seconds: 40, source: 'worker', status: 'completed' },
      upload: { seconds: 15, source: 'events', status: 'in_progress' },
    },
  }), [
    { key: 'restore_env', label: '恢复运行环境', value: '40秒', source: '运行时记录', status: 'completed' },
    { key: 'upload', label: '保存产物', value: '进行中 · 已用15秒', source: '持久事件', status: 'in_progress' },
  ]);
});
