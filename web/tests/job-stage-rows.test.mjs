import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { transform } from 'esbuild';

test('stage rows order lifecycle and omit invalid measured durations', async () => {
  const source = readFileSync('src/lib/job-stage-rows.ts', 'utf8');
  const { code } = await transform(source, { loader: 'ts', format: 'esm' });
  const { jobStageRows } = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
  assert.deepEqual(jobStageRows({ sampling: 328.7, allocate: 11.2, restore_weights: 1072.3, bad: -1, upload: null }), [
    { key: 'allocate', label: '分配算力', seconds: 11.2 },
    { key: 'restore_weights', label: '恢复模型权重', seconds: 1072.3 },
    { key: 'sampling', label: '采样生成', seconds: 328.7 },
  ]);
  assert.deepEqual(jobStageRows({ waiting_for_capacity: 7 }), [
    { key: 'waiting_for_capacity', label: '等待算力', seconds: 7 },
  ]);
  assert.deepEqual(jobStageRows(null), []);
  assert.deepEqual(jobStageRows({ warm_reuse: 0, future_stage: 3 }), [
    { key: 'warm_reuse', label: '复用已就绪环境', seconds: 0 },
    { key: 'future_stage', label: 'future_stage', seconds: 3 },
  ]);
});

test('history labels translate known states and stages while preserving unknown values', async () => {
  const source = readFileSync('src/lib/job-stage-rows.ts', 'utf8');
  const { code } = await transform(source, { loader: 'ts', format: 'esm' });
  const { jobStateLabel, jobStageLabel } = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
  assert.equal(jobStateLabel('completed'), '已完成');
  assert.equal(jobStateLabel('cleanup_pending'), '待清理');
  assert.equal(jobStateLabel('future_state'), 'future_state');
  assert.equal(jobStateLabel(undefined), '状态未知');
  assert.equal(jobStageLabel('sampling'), '采样生成');
  assert.equal(jobStageLabel('future_stage'), 'future_stage');
});
